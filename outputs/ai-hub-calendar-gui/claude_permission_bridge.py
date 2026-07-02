from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


TOOL_NAME = "mcp_auth_tool"


def log(message: str) -> None:
    print(f"[ai-hub-permission] {message}", file=sys.stderr, flush=True)


def make_allow(tool_use_id: str = "", updated_input: dict | None = None) -> dict:
    result: dict = {
        "behavior": "allow",
        "updatedInput": updated_input or {},
        "decisionClassification": "user_temporary",
    }
    if tool_use_id:
        result["toolUseID"] = tool_use_id
    return result


def make_deny(message: str, tool_use_id: str = "", interrupt: bool = False) -> dict:
    result: dict = {
        "behavior": "deny",
        "message": message,
        "interrupt": interrupt,
        "decisionClassification": "user_reject",
    }
    if tool_use_id:
        result["toolUseID"] = tool_use_id
    return result


def call_hub(payload: dict) -> dict:
    url = os.environ.get("AI_HUB_PERMISSION_URL", "").strip()
    token = os.environ.get("AI_HUB_PERMISSION_TOKEN", "").strip()
    tool_use_id = str(payload.get("tool_use_id") or "")
    if not url:
        fallback = os.environ.get("AI_HUB_PERMISSION_FALLBACK", "deny").strip().lower()
        if fallback == "allow":
            return make_allow(tool_use_id)
        return make_deny("AI Account Hub permission bridge is not connected.", tool_use_id)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=310) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError) as error:
        return make_deny(f"AI Account Hub permission bridge failed: {error}", tool_use_id)
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return make_deny("AI Account Hub returned an invalid permission response.", tool_use_id)
    if isinstance(result, dict) and result.get("behavior") in {"allow", "deny"}:
        if result.get("behavior") == "allow":
            result.setdefault("updatedInput", {})
            result.setdefault("decisionClassification", "user_temporary")
        else:
            result.setdefault("message", "Denied by AI Account Hub.")
            result.setdefault("decisionClassification", "user_reject")
        if tool_use_id and not result.get("toolUseID"):
            result["toolUseID"] = tool_use_id
        return result
    return make_deny("AI Account Hub returned an unsupported permission decision.", tool_use_id)


def respond(request_id: object, result: dict | None = None, error: dict | None = None) -> None:
    message: dict = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result or {}
    print(json.dumps(message, separators=(",", ":"), ensure_ascii=False), flush=True)


def handle_request(message: dict) -> None:
    method = str(message.get("method") or "")
    request_id = message.get("id")
    if method == "initialize":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        protocol = str(params.get("protocolVersion") or "2025-03-26")
        respond(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ai-account-hub-permissions", "version": "0.1.0"},
            },
        )
        return
    if method == "tools/list":
        respond(
            request_id,
            {
                "tools": [
                    {
                        "name": TOOL_NAME,
                        "description": "Ask AI Account Hub whether Claude Code may use a requested tool.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "tool_name": {"type": "string"},
                                "input": {"type": "object"},
                                "tool_use_id": {"type": "string"},
                            },
                            "required": ["tool_name", "input"],
                            "additionalProperties": True,
                        },
                    }
                ]
            },
        )
        return
    if method == "tools/call":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if name != TOOL_NAME:
            respond(request_id, error={"code": -32602, "message": f"Unknown tool: {name}"})
            return
        decision = call_hub(arguments)
        respond(
            request_id,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(decision, separators=(",", ":"), ensure_ascii=False),
                    }
                ]
            },
        )
        return
    if request_id is not None:
        respond(request_id, error={"code": -32601, "message": f"Unsupported method: {method}"})


def main() -> int:
    log("ready")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            log(f"ignored non-json line: {line[:120]}")
            continue
        if isinstance(message, dict):
            try:
                handle_request(message)
            except Exception as error:  # pragma: no cover - final guard for MCP host stability
                request_id = message.get("id")
                if request_id is not None:
                    respond(request_id, error={"code": -32000, "message": str(error)})
                log(f"request failed: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
