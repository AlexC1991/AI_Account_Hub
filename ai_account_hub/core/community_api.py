"""Privacy-bounded community telemetry API and signed Cloudflare transport."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from ai_account_hub.core.community_identity import (
    CommunityIdentity,
    CommunityIdentityError,
    CommunityIdentityStore,
)


SCHEMA_VERSION = 1
# Version 2 is the first consent that permits an actual network submission.
# Version 1 belonged to the offline test adapter and must never migrate silently.
CONSENT_VERSION = 2
TEST_API_URL = "test://local/community/v1"
DEFAULT_COMMUNITY_API_URL = (
    "https://ai-account-hub-community-api-staging."
    "acca-gaming-productions.workers.dev"
)

_ENVELOPE_FIELDS = {
    "schemaVersion", "periodStartUtc", "periodEndUtc", "source", "records",
}
_RECORD_FIELDS = {
    "provider", "modelId", "reasoningEffort", "totalTokens",
    "completedTasks", "activeMs", "edits", "filesChanged", "tests",
    "commands", "shortBurn", "weeklyBurn",
}
_TEXT_FIELDS = {"provider", "modelId", "reasoningEffort"}
_NUMBER_FIELDS = _RECORD_FIELDS - _TEXT_FIELDS


class CommunityApiError(ValueError):
    """Raised when a community payload or adapter operation is unsafe."""


class CommunityApi(Protocol):
    mode: str
    endpoint: str
    supports_submissions: bool

    def fetch_results(self, *, days: int = 30, provider: str = "all") -> dict: ...

    def submit(self, payload: dict) -> dict: ...

    def withdraw(self) -> dict: ...


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def validate_submission(payload: dict) -> dict:
    """Return a normalized copy or reject any non-allowlisted field.

    A strict schema is deliberate: adding a field to a caller does not silently
    make that field uploadable. The public Cloudflare adapter will reuse this
    validator before signing or sending a request.
    """

    if not isinstance(payload, dict):
        raise CommunityApiError("Community payload must be an object")
    unexpected = set(payload) - _ENVELOPE_FIELDS
    if unexpected:
        raise CommunityApiError(f"Unexpected community fields: {', '.join(sorted(unexpected))}")
    if int(payload.get("schemaVersion") or 0) != SCHEMA_VERSION:
        raise CommunityApiError("Unsupported community schema version")
    if str(payload.get("source") or "") != "ai-account-hub":
        raise CommunityApiError("Unknown community payload source")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise CommunityApiError("Community payload must contain aggregate records")
    if len(records) > 100:
        raise CommunityApiError("Community payload contains too many records")

    normalized_records = []
    record_keys: set[str] = set()
    for source in records:
        if not isinstance(source, dict):
            raise CommunityApiError("Each community record must be an object")
        extra = set(source) - _RECORD_FIELDS
        if extra:
            raise CommunityApiError(f"Unexpected record fields: {', '.join(sorted(extra))}")
        record: dict[str, object] = {}
        for key in _TEXT_FIELDS:
            value = str(source.get(key) or "").strip()
            if key != "reasoningEffort" and not value:
                raise CommunityApiError(f"Community record is missing {key}")
            if len(value) > 96:
                raise CommunityApiError(f"Community field {key} is too long")
            record[key] = value
        for key in _NUMBER_FIELDS:
            value = source.get(key, 0)
            if isinstance(value, bool):
                raise CommunityApiError(f"Community field {key} must be numeric")
            try:
                number = float(value or 0)
            except (TypeError, ValueError) as exc:
                raise CommunityApiError(f"Community field {key} must be numeric") from exc
            if number < 0 or number > 2**53 - 1:
                raise CommunityApiError(f"Community field {key} is outside its safe range")
            record[key] = int(number) if number.is_integer() else round(number, 4)
        record_key = "\0".join((
            str(record["provider"]).lower(),
            str(record["modelId"]).lower(),
            str(record["reasoningEffort"]).lower(),
        ))
        if record_key in record_keys:
            raise CommunityApiError("Community payload duplicates a model and reasoning setting")
        record_keys.add(record_key)
        normalized_records.append(record)

    output = {
        "schemaVersion": SCHEMA_VERSION,
        "periodStartUtc": str(payload.get("periodStartUtc") or ""),
        "periodEndUtc": str(payload.get("periodEndUtc") or ""),
        "source": "ai-account-hub",
        "records": normalized_records,
    }
    parsed_dates = []
    for field in ("periodStartUtc", "periodEndUtc"):
        try:
            parsed_dates.append(
                dt.datetime.fromisoformat(str(output[field]).replace("Z", "+00:00"))
            )
        except ValueError as exc:
            raise CommunityApiError(f"Community field {field} must be ISO-8601") from exc
    start, end = parsed_dates
    if start.utcoffset() != dt.timedelta(0) or end.utcoffset() != dt.timedelta(0):
        raise CommunityApiError("Community period must use UTC")
    if start.time() != dt.time.min or end - start != dt.timedelta(days=1):
        raise CommunityApiError("Community period must be one exact UTC day")
    return output


def build_submission_payload(groups: list[dict], *, days: int = 1) -> dict:
    """Build an anonymous, numeric aggregate from local Statistics groups."""

    span = max(1, min(int(days or 1), 31))
    available_days = sorted({
        str(day)
        for group in groups
        for day in (group.get("days") or {})
        if str(day)
    })
    if available_days:
        latest_day = dt.date.fromisoformat(available_days[-1])
        selected_days = {
            (latest_day - dt.timedelta(days=offset)).isoformat()
            for offset in range(span)
        }
        start = dt.datetime.combine(
            latest_day - dt.timedelta(days=span - 1), dt.time.min, dt.timezone.utc
        )
        end = dt.datetime.combine(latest_day + dt.timedelta(days=1), dt.time.min, dt.timezone.utc)
    else:
        latest_day = _utc_now().date()
        start = dt.datetime.combine(latest_day, dt.time.min, dt.timezone.utc)
        end = start + dt.timedelta(days=1)
        selected_days = set()
    records = []
    for group in groups:
        provider = str(group.get("provider") or "").strip().lower()
        model_id = str(group.get("modelId") or "").strip()
        if not provider or not model_id:
            continue
        buckets = [
            bucket
            for day, bucket in (group.get("days") or {}).items()
            if str(day) in selected_days and isinstance(bucket, dict)
        ]
        if available_days and not buckets:
            continue

        def aggregate(field: str, alias: str = "") -> float:
            if buckets:
                return sum(float(bucket.get(field, bucket.get(alias, 0)) or 0) for bucket in buckets)
            return float(group.get(field) or 0)

        records.append({
            "provider": provider,
            "modelId": model_id,
            "reasoningEffort": str(group.get("reasoningEffort") or "").strip().lower(),
            # Per-provider-account views can legitimately contain fractional
            # means. Preserve them through validation instead of truncating a
            # two-account average back to an integer.
            "totalTokens": max(0.0, round(aggregate("totalTokens", "tokens"), 4)),
            "completedTasks": max(0.0, round(aggregate("completedTasks", "tasks"), 4)),
            "activeMs": max(0.0, round(aggregate("activeMs"), 4)),
            "edits": max(0.0, round(aggregate("edits"), 4)),
            "filesChanged": max(0.0, round(aggregate("filesChanged", "files"), 4)),
            "tests": max(0.0, round(aggregate("tests"), 4)),
            "commands": max(0.0, round(aggregate("commands"), 4)),
            "shortBurn": max(0.0, aggregate("shortBurn")),
            "weeklyBurn": max(0.0, aggregate("weeklyBurn")),
        })
    if not records:
        # An empty local history should not create a meaningless community row.
        raise CommunityApiError("No aggregate model activity is available to share")
    return validate_submission({
        "schemaVersion": SCHEMA_VERSION,
        "periodStartUtc": start.isoformat().replace("+00:00", "Z"),
        "periodEndUtc": end.isoformat().replace("+00:00", "Z"),
        "source": "ai-account-hub",
        "records": records,
    })


@dataclass(frozen=True)
class _ModelSpec:
    provider: str
    model_id: str
    model_name: str
    reasoning: str
    reasoning_name: str
    tasks_per_5h: float
    tokens_per_task: int
    weekly_per_task: float
    observations: int
    contributors: int


_TEST_MODELS = (
    _ModelSpec("claude", "claude-opus-4-8", "Claude Opus 4.8", "standard", "Standard", 8.4, 3_200_000, 1.8, 2840, 103),
    _ModelSpec("claude", "claude-sonnet-5", "Claude Sonnet 5", "standard", "Standard", 6.9, 4_800_000, 2.3, 1620, 78),
    _ModelSpec("codex", "gpt-5.5", "GPT-5.5", "high", "High", 9.1, 5_700_000, 2.0, 3902, 119),
    _ModelSpec("codex", "gpt-5.5", "GPT-5.5", "xhigh", "XHigh", 7.7, 7_800_000, 2.8, 3275, 111),
    _ModelSpec("codex", "gpt-5.6-sol", "GPT-5.6 Sol", "ultra", "Ultra", 4.9, 9_300_000, 3.7, 771, 42),
)


def _synthetic_waves(offset: int, model_index: int) -> tuple[float, float, float]:
    """Create distinct daily sample movement without pretending it is real data."""
    phase = model_index * 0.83
    throughput = 0.88 + 0.11 * math.sin(offset * 0.71 + phase) + 0.04 * math.cos(offset * 0.29 + phase)
    token_cost = 1.0 + 0.08 * math.sin(offset * 0.43 + phase + 1.2) + 0.025 * math.cos(offset * 0.17 + phase)
    weekly_cost = 1.0 + 0.10 * math.cos(offset * 0.37 + phase + 0.4)
    return throughput, token_cost, weekly_cost


def _dynamicize_staging_sample(payload: dict) -> None:
    """Upgrade the old static R2 preview while leaving real cohorts untouched."""
    if str(payload.get("dataSource") or "") != "synthetic-staging" or payload.get("syntheticDynamic"):
        return
    for model_index, group in enumerate(payload.get("groups") or []):
        if not isinstance(group, dict) or not isinstance(group.get("days"), dict):
            continue
        source_days = sorted(group["days"].items())
        if not source_days:
            continue
        base_tokens = float(group.get("tokensPerTask") or 0)
        base_session = float(group.get("tasksPerSession") or 0)
        base_weekly = float(group.get("weeklyBurnPerTask") or 0)
        end = _utc_now().date()
        dynamic_days = {}
        for offset, (_day, source) in enumerate(source_days):
            bucket = dict(source) if isinstance(source, dict) else {}
            throughput_wave, token_wave, weekly_wave = _synthetic_waves(offset, model_index)
            tasks = float(bucket.get("tasks") or bucket.get("observations") or 0)
            tasks_per_session = max(0.01, base_session * throughput_wave)
            tokens_per_task = max(1.0, base_tokens * token_wave)
            weekly_per_task = max(0.01, base_weekly * weekly_wave)
            bucket.update({
                "tokens": tasks * tokens_per_task,
                "shortBurn": tasks * 100 / tasks_per_session if tasks else 0,
                "weeklyBurn": tasks * weekly_per_task,
                "tasksPerSession": round(tasks_per_session, 4),
                "tokensPerTask": round(tokens_per_task, 4),
                "weeklyBurnPerTask": round(weekly_per_task, 4),
            })
            day = end - dt.timedelta(days=len(source_days) - offset - 1)
            dynamic_days[day.isoformat()] = bucket
        group["days"] = dynamic_days
    payload["syntheticDynamic"] = True


class TestCommunityApi:
    """Deterministic offline adapter used by Help demo and local UI tests."""

    mode = "test"
    endpoint = TEST_API_URL
    supports_submissions = True

    def __init__(self) -> None:
        self._submissions: list[dict] = []

    def fetch_results(self, *, days: int = 30, provider: str = "all") -> dict:
        days = max(7, min(int(days or 30), 365))
        provider = str(provider or "all").lower()
        end = _utc_now().date()
        start = end - dt.timedelta(days=days - 1)
        groups = []
        for model_index, spec in enumerate(_TEST_MODELS):
            if provider not in {"all", spec.provider}:
                continue
            buckets = {}
            task_total = 0
            token_total = 0
            short_burn_total = 0.0
            weekly_burn_total = 0.0
            observation_total = 0
            for offset in range(days):
                day = start + dt.timedelta(days=offset)
                throughput_wave, token_wave, weekly_wave = _synthetic_waves(offset, model_index)
                daily_observations = max(1, round(spec.observations / 30 * throughput_wave))
                tasks_per_session = max(0.01, spec.tasks_per_5h * throughput_wave)
                tokens_per_task = max(1, round(spec.tokens_per_task * token_wave))
                weekly_per_task = max(0.01, spec.weekly_per_task * weekly_wave)
                daily_tasks = max(1, round(daily_observations * tasks_per_session / 24))
                daily_tokens = daily_tasks * tokens_per_task
                daily_short_burn = daily_tasks * 100 / tasks_per_session
                daily_weekly_burn = daily_tasks * weekly_per_task
                task_total += daily_tasks
                token_total += daily_tokens
                short_burn_total += daily_short_burn
                weekly_burn_total += daily_weekly_burn
                observation_total += daily_observations
                buckets[day.isoformat()] = {
                    "tokens": daily_tokens,
                    "tasks": daily_tasks,
                    "shortBurn": daily_short_burn,
                    "weeklyBurn": daily_weekly_burn,
                    "tasksPerSession": round(tasks_per_session, 2),
                    "tokensPerTask": tokens_per_task,
                    "weeklyBurnPerTask": round(weekly_per_task, 4),
                    "observations": daily_observations,
                }
            label = spec.model_name
            if spec.reasoning_name:
                label = f"{label} - {spec.reasoning_name}"
            aggregate_session = task_total * 100 / short_burn_total
            aggregate_tokens = token_total / task_total
            aggregate_weekly = weekly_burn_total / task_total
            groups.append({
                "provider": spec.provider,
                "modelId": spec.model_id,
                "modelName": spec.model_name,
                "modelLabel": label,
                "reasoningEffort": spec.reasoning,
                "reasoningEffortName": spec.reasoning_name,
                "filterKey": f"community|{spec.provider}|{spec.model_id}|{spec.reasoning}",
                "totalTokens": token_total,
                "completedTasks": task_total,
                "shortBurn": round(short_burn_total, 1),
                "weeklyBurn": round(weekly_burn_total, 1),
                "tasksPerSession": aggregate_session,
                "tokensPerTask": aggregate_tokens,
                "weeklyBurnPerTask": aggregate_weekly,
                "observations": observation_total,
                "contributors": spec.contributors,
                "normalized": {
                    "tokensPerCompletedTask": aggregate_tokens,
                    "tasksPerMillionTokens": 1_000_000 / aggregate_tokens,
                    "tasksPerSession": aggregate_session,
                },
                "days": buckets,
            })
        return {
            "schemaVersion": SCHEMA_VERSION,
            "mode": self.mode,
            "dataSource": "offline-test",
            "endpoint": self.endpoint,
            "generatedAtUtc": _utc_now().isoformat().replace("+00:00", "Z"),
            "periodDays": days,
            "contributors": 146 if provider == "all" and groups else max(
                [int(group.get("contributors") or 0) for group in groups] or [0]
            ),
            "observedTasks": sum(int(group["completedTasks"]) for group in groups),
            "collectionContributors": 0,
            "collectionSubmissions": 0,
            "minimumContributors": 0,
            "groups": groups,
        }

    def submit(self, payload: dict) -> dict:
        normalized = validate_submission(payload)
        canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        receipt_id = "test_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        self._submissions.append(normalized)
        return {
            "accepted": True,
            "mode": self.mode,
            "endpoint": self.endpoint,
            "receiptId": receipt_id,
            "acceptedAtUtc": _utc_now().isoformat().replace("+00:00", "Z"),
            "recordCount": len(normalized["records"]),
            "networkRequest": False,
        }

    def withdraw(self) -> dict:
        deleted = len(self._submissions)
        self._submissions.clear()
        return {
            "withdrawn": True,
            "mode": self.mode,
            "endpoint": self.endpoint,
            "deletedSubmissions": deleted,
            "networkRequest": False,
        }

    @property
    def submissions(self) -> list[dict]:
        return [dict(item) for item in self._submissions]


class CloudflareCommunityApi:
    """Public aggregate reader and signed, opt-in submission client.

    The desktop knows only the Worker URL. Its private signing key stays in a
    DPAPI-protected machine-local file and no Cloudflare credential is shipped.
    """

    mode = "cloudflare-staging"
    supports_submissions = True
    _MAX_RESPONSE_BYTES = 512 * 1024

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 10.0,
        identity_path: Path | None = None,
    ) -> None:
        endpoint = str(base_url or "").strip().rstrip("/")
        parsed = urlparse(endpoint)
        local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme not in ({"http", "https"} if local else {"https"}):
            raise CommunityApiError("Community Worker URL must use HTTPS")
        if not parsed.hostname or parsed.hostname.endswith("r2.cloudflarestorage.com"):
            raise CommunityApiError(
                "Use the Cloudflare Worker URL, never the direct R2 S3 endpoint"
            )
        self.endpoint = endpoint
        self._timeout = max(1.0, min(float(timeout_seconds), 30.0))
        launcher_root = Path(
            os.environ.get(
                "AI_HUB_LAUNCHER_ROOT",
                str(Path.home() / ".codex-account-launcher"),
            )
        ).expanduser()
        self._identity_store = CommunityIdentityStore(
            Path(identity_path) if identity_path else launcher_root / "community-identity.json"
        )

    def fetch_results(self, *, days: int = 30, provider: str = "all") -> dict:
        requested_days = max(7, min(int(days or 30), 365))
        query = urlencode({
            "days": requested_days,
            "provider": str(provider or "all").lower(),
        })
        payload = self._request_json(
            "GET", f"/v1/community/models?{query}", max_bytes=self._MAX_RESPONSE_BYTES
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
            raise CommunityApiError("Community Worker returned an invalid aggregate shape")
        if len(payload["groups"]) > 100:
            raise CommunityApiError("Community Worker returned too many model groups")
        _dynamicize_staging_sample(payload)
        provider = str(provider or "all").lower()
        if provider != "all":
            payload["groups"] = [
                group for group in payload["groups"]
                if isinstance(group, dict) and str(group.get("provider") or "").lower() == provider
            ]
        for group in payload["groups"]:
            if not isinstance(group, dict) or not isinstance(group.get("days"), dict):
                continue
            selected_keys = sorted(str(day) for day in group["days"])[-requested_days:]
            group["days"] = {
                day: group["days"][day] for day in selected_keys
                if isinstance(group["days"].get(day), dict)
            }
            buckets = list(group["days"].values())
            tasks = sum(float(bucket.get("tasks") or 0) for bucket in buckets)
            group["totalTokens"] = sum(float(bucket.get("tokens") or 0) for bucket in buckets)
            group["completedTasks"] = tasks
            observed_tasks = sum(
                float(bucket.get("observations") or 0) for bucket in buckets
            )
            group["observations"] = observed_tasks
            group["shortBurn"] = sum(float(bucket.get("shortBurn") or 0) for bucket in buckets)
            group["weeklyBurn"] = sum(float(bucket.get("weeklyBurn") or 0) for bucket in buckets)

            # Rebuild ratios from the selected daily window. The public object
            # spans up to a year, but a 7/30/90-day client selection must not
            # retain the full-period leaderboard result.
            weighted_tokens = sum(
                float(bucket.get("tokensPerTask") or 0)
                * float(bucket.get("observations") or 0)
                for bucket in buckets
            )
            weighted_weekly = sum(
                float(bucket.get("weeklyBurnPerTask") or 0)
                * float(bucket.get("observations") or 0)
                for bucket in buckets
            )
            weighted_short_burn = sum(
                float(bucket.get("observations") or 0) * 100
                / float(bucket.get("tasksPerSession") or 1)
                for bucket in buckets
                if float(bucket.get("tasksPerSession") or 0) > 0
            )
            group["tokensPerTask"] = (
                weighted_tokens / observed_tasks if observed_tasks else 0
            )
            group["weeklyBurnPerTask"] = (
                weighted_weekly / observed_tasks if observed_tasks else 0
            )
            group["tasksPerSession"] = (
                observed_tasks * 100 / weighted_short_burn if weighted_short_burn else 0
            )
            group["normalized"] = {
                "tokensPerCompletedTask": group["tokensPerTask"],
                "tasksPerMillionTokens": (
                    observed_tasks * 1_000_000 / weighted_tokens
                    if weighted_tokens else 0
                ),
                "tasksPerSession": group["tasksPerSession"],
            }
        payload["observedTasks"] = sum(
            float(group.get("observations") or 0)
            for group in payload["groups"] if isinstance(group, dict)
        )
        provider_counts = payload.get("providerContributors")
        if provider != "all" and isinstance(provider_counts, dict):
            payload["contributors"] = int(provider_counts.get(provider) or 0)
        payload["periodDays"] = requested_days
        payload.setdefault("mode", self.mode)
        payload.setdefault("endpoint", self.endpoint)
        return payload

    def submit(self, payload: dict) -> dict:
        normalized = validate_submission(payload)
        try:
            identity = self._identity_store.load_or_create()
        except CommunityIdentityError as exc:
            raise CommunityApiError(str(exc)) from exc
        self._register(identity)
        body = json.dumps(
            normalized, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        receipt = self._signed_request(
            identity, "POST", "/v1/submissions", body=body
        )
        receipt.setdefault("mode", self.mode)
        receipt.setdefault("endpoint", self.endpoint)
        receipt["networkRequest"] = True
        return receipt

    def withdraw(self) -> dict:
        """Delete this installation's accepted raw submissions and local key."""

        try:
            identity = self._identity_store.load()
        except CommunityIdentityError as exc:
            raise CommunityApiError(str(exc)) from exc
        if identity is None:
            return {
                "withdrawn": True,
                "mode": self.mode,
                "endpoint": self.endpoint,
                "deletedSubmissions": 0,
                "networkRequest": False,
            }
        result = self._signed_request(
            identity, "DELETE", "/v1/installations/me", body=b""
        )
        self._identity_store.delete()
        result.setdefault("mode", self.mode)
        result.setdefault("endpoint", self.endpoint)
        result["networkRequest"] = True
        return result

    @property
    def installation_id(self) -> str:
        try:
            identity = self._identity_store.load()
        except CommunityIdentityError:
            return ""
        return identity.installation_id if identity else ""

    def _register(self, identity: CommunityIdentity) -> dict:
        timestamp, nonce = self._fresh_auth_values()
        canonical = (
            f"AIH1-REGISTER\n{identity.public_key}\n{timestamp}\n{nonce}"
        ).encode("utf-8")
        body = json.dumps(
            {"publicKey": identity.public_key}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return self._request_json(
            "POST",
            "/v1/installations",
            body=body,
            headers={
                "X-AIH-Timestamp": timestamp,
                "X-AIH-Nonce": nonce,
                "X-AIH-Signature": identity.sign(canonical),
            },
        )

    def _signed_request(
        self,
        identity: CommunityIdentity,
        method: str,
        path: str,
        *,
        body: bytes,
    ) -> dict:
        timestamp, nonce = self._fresh_auth_values()
        body_hash = hashlib.sha256(body).hexdigest()
        canonical = (
            f"AIH1\n{method}\n{path}\n{identity.installation_id}\n"
            f"{timestamp}\n{nonce}\n{body_hash}"
        ).encode("utf-8")
        return self._request_json(
            method,
            path,
            body=body,
            headers={
                "X-AIH-Installation": identity.installation_id,
                "X-AIH-Timestamp": timestamp,
                "X-AIH-Nonce": nonce,
                "X-AIH-Body-SHA256": body_hash,
                "X-AIH-Signature": identity.sign(canonical),
            },
        )

    @staticmethod
    def _fresh_auth_values() -> tuple[str, str]:
        return str(int(time.time())), secrets.token_urlsafe(24)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        max_bytes: int = 64 * 1024,
    ) -> dict:
        request_headers = {
            "accept": "application/json",
            "user-agent": "AI-Account-Hub/1",
        }
        if body is not None:
            request_headers["content-type"] = "application/json"
        request_headers.update(headers or {})
        request = Request(
            f"{self.endpoint}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                response_body = response.read(max_bytes + 1)
        except HTTPError as exc:
            error_body = exc.read(64 * 1024)
            message = f"Community Worker rejected the request ({exc.code})"
            try:
                parsed = json.loads(error_body.decode("utf-8"))
                message = str(parsed.get("error", {}).get("message") or message)
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass
            raise CommunityApiError(message) from exc
        except (URLError, OSError) as exc:
            raise CommunityApiError(f"Community Worker is unavailable: {exc}") from exc
        if len(response_body) > max_bytes:
            raise CommunityApiError("Community Worker response is too large")
        try:
            parsed = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommunityApiError("Community Worker returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise CommunityApiError("Community Worker returned an invalid response")
        return parsed


def configured_community_api() -> CommunityApi:
    """Select the safe adapter without persisting deployment URLs in profiles."""

    endpoint = str(os.environ.get("AI_HUB_COMMUNITY_API_URL") or "").strip()
    if os.environ.get("AI_HUB_DEMO") == "1" or endpoint.lower() in {"test", TEST_API_URL}:
        return TestCommunityApi()
    return CloudflareCommunityApi(endpoint or DEFAULT_COMMUNITY_API_URL)
