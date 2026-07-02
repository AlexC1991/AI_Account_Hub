"""Account/profile management and coding-controls sync for the Hub UI.

Extracted from AccountCalendarApp. Holds no state of its own beyond
``self.app`` -- every reference to shared app state (and every call between
two moved methods) goes through ``self.app.X``, matching the same design as
coding_transport_bridge.py (see that module's docstring for the full
rationale: it lets instance-level test monkeypatching keep working, and
avoids a circular import that breaks under the test suite's module loader).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk

_HUB_STATIC_NAMES = (
    "ANTIGRAVITY_ROAMING_HOME",
    "CARD_TEMPLATE_CHOICES",
    "CLAUDE_ROAMING_HOME",
    "CODING_ACCESS_OPTIONS",
    "CODING_EFFORT_OPTIONS",
    "CODING_FALLBACK_MODELS",
    "CODING_PERSONALITY_OPTIONS",
    "CURSOR_ROAMING_HOME",
    "DEFAULT_ACCOUNTS_ROOT",
    "DESKTOP_ACTIVE_PROFILE_PATH",
    "HUB_ACCOUNTS_ROOT",
    "INK",
    "MUTED",
    "PANEL",
    "PANEL_ALT",
    "PRIMARY",
    "PROVIDER_CHOICES",
    "SORT_CHOICES",
    "browser_profile_mode",
    "effective_state",
    "ensure_profile_home",
    "load_profiles",
    "normalize_profile",
    "parse_custom_online_links_text",
    "parse_iso_datetime",
    "percent_left",
    "profile_id",
    "provider_key",
    "provider_label",
    "read_coding_profile_defaults",
    "save_settings",
    "serialize_online_links_text",
)
_HUB_LIVE_CONSTANT_NAMES: tuple[str, ...] = ()

_hub_globals: dict = {}


def configure_helpers(hub_globals: dict) -> None:
    """Wire up ai_hub_calendar_gui.py's small helpers.

    Called once by ai_hub_calendar_gui.py immediately after it imports
    AccountManager from this module, passing its own globals(). See
    coding_transport_bridge.py's module docstring for why this replaces a
    direct circular import.
    """
    missing = (set(_HUB_STATIC_NAMES) | set(_HUB_LIVE_CONSTANT_NAMES)) - set(hub_globals)
    if missing:
        raise RuntimeError(f"configure_helpers missing required names: {sorted(missing)}")
    globals().update({name: hub_globals[name] for name in _HUB_STATIC_NAMES})
    global _hub_globals
    _hub_globals = hub_globals


class AccountManager:
    """Account/profile CRUD, selection, and coding-controls sync. Holds no
    state of its own; all shared state lives on ``self.app``.
    """

    def __init__(self, app) -> None:
        self.app = app

    def coding_profile_options(self) -> dict[str, str]:
        options: dict[str, str] = {}
        label_counts: dict[str, int] = {}
        for profile in self.app.profiles:
            base = f"{provider_label(profile)} | {profile.get('name', 'Account')}"
            label_counts[base] = label_counts.get(base, 0) + 1
            label = base if label_counts[base] == 1 else f"{base} ({label_counts[base]})"
            options[label] = profile_id(profile)
        return options

    def coding_selected_profile(self) -> dict | None:
        for profile in self.app.profiles:
            if profile_id(profile) == self.app.coding_profile_id:
                return profile
        return None

    def _sync_coding_profile_combo(self) -> None:
        options = self.app.coding_profile_options()
        self.app.coding_profile_option_map = options
        available_ids = set(options.values())
        if self.app.coding_profile_id not in available_ids:
            ready = next((profile for profile in self.app.profiles if effective_state(profile) == "ready"), None)
            self.app.coding_profile_id = profile_id(ready) if ready is not None else (next(iter(available_ids), ""))
        selected_label = next((label for label, pid in options.items() if pid == self.app.coding_profile_id), "")
        if hasattr(self.app, "coding_profile_combo"):
            self.app.coding_profile_combo.configure(values=list(options))
        self.app.coding_profile_var.set(selected_label)

    def _coding_control_preferences(self, profile: dict) -> dict[str, str]:
        controls = self.app.settings.get("codingControls")
        saved_controls = controls if isinstance(controls, dict) else {}
        saved = saved_controls.get(profile_id(profile))
        pid = profile_id(profile)
        if pid not in self.app._coding_defaults_cache:
            self.app._coding_defaults_cache[pid] = read_coding_profile_defaults(profile)
        defaults = self.app._coding_defaults_cache[pid]
        result = {
            "model": str(defaults.get("model") or ""),
            "effort": str(defaults.get("effort") or ""),
            "access": str(defaults.get("access") or CODING_ACCESS_OPTIONS.get(provider_key(profile), [("Default", "default")])[0][1]),
            "personality": str(defaults.get("personality") or ("friendly" if provider_key(profile) == "codex" else "")),
        }
        if isinstance(saved, dict):
            for key in ("model", "effort", "access", "personality"):
                if key in saved:
                    result[key] = str(saved.get(key) or "")
        return result

    def _save_coding_control_preference(self, profile: dict, key: str, value: str) -> None:
        controls = self.app.settings.get("codingControls")
        if not isinstance(controls, dict):
            controls = {}
            self.app.settings["codingControls"] = controls
        saved = controls.get(profile_id(profile))
        if not isinstance(saved, dict):
            saved = {}
            controls[profile_id(profile)] = saved
        saved[key] = value
        save_settings(self.app.settings)

    def _coding_model_rows(self, profile: dict) -> list[tuple[str, str]]:
        provider = provider_key(profile)
        if self.app.native_models_profile_id == profile_id(profile) and self.app.native_models:
            rows: list[tuple[str, str]] = []
            seen: set[str] = set()
            for model in self.app.native_models:
                value = str(model.get("model") or model.get("id") or "").strip()
                if not value or value.lower() in seen:
                    continue
                seen.add(value.lower())
                label = str(model.get("displayName") or value).strip()
                if model.get("isDefault"):
                    label = f"{label} (default)"
                rows.append((label, value))
            if rows:
                return rows
        return list(CODING_FALLBACK_MODELS.get(provider, [("Default", "")]))

    def _coding_effort_rows(self, profile: dict, model_value: str) -> list[tuple[str, str]]:
        provider = provider_key(profile)
        if provider == "codex" and self.app.native_models_profile_id == profile_id(profile):
            model = next(
                (
                    item
                    for item in self.app.native_models
                    if str(item.get("model") or item.get("id") or "") == model_value
                ),
                None,
            )
            if isinstance(model, dict):
                supported: list[tuple[str, str]] = []
                default_effort = str(model.get("defaultReasoningEffort") or "")
                if default_effort:
                    supported.append((f"Default ({default_effort.title()})", ""))
                for item in model.get("supportedReasoningEfforts") or []:
                    value = (
                        str(item.get("reasoningEffort") or item.get("effort") or item.get("value") or "")
                        if isinstance(item, dict)
                        else str(item)
                    )
                    if value and value not in {row[1] for row in supported}:
                        supported.append((value.replace("-", " ").title(), value))
                if supported:
                    return supported
        return list(CODING_EFFORT_OPTIONS.get(provider, [("Model default", "")]))

    def _sync_coding_controls(self) -> None:
        profile = self.app.coding_selected_profile()
        if profile is None:
            return
        preferences = self.app._coding_control_preferences(profile)
        model_rows = self.app._coding_model_rows(profile)
        model_values = {value for _label, value in model_rows}
        model_value = preferences["model"]
        if not model_value and self.app.native_models_profile_id == profile_id(profile):
            default_model = next(
                (
                    str(item.get("model") or item.get("id") or "")
                    for item in self.app.native_models
                    if item.get("isDefault")
                ),
                "",
            )
            model_value = default_model
        if model_value and model_value not in model_values:
            model_rows.append((model_value, model_value))
        self.app.coding_model_options = dict(model_rows)
        self.app.coding_model_var.set(self.app._coding_option_label(self.app.coding_model_options, model_value))

        effort_rows = self.app._coding_effort_rows(profile, model_value)
        effort_value = preferences["effort"]
        if effort_value and effort_value not in {value for _label, value in effort_rows}:
            effort_rows.append((effort_value.replace("-", " ").title(), effort_value))
        self.app.coding_effort_options = dict(effort_rows)
        self.app.coding_effort_var.set(self.app._coding_option_label(self.app.coding_effort_options, effort_value))

        personality_rows = CODING_PERSONALITY_OPTIONS.get(provider_key(profile), [("Provider default", "")])
        personality_value = preferences["personality"]
        if personality_value not in {value for _label, value in personality_rows}:
            personality_value = personality_rows[0][1]
        self.app.coding_personality_options = dict(personality_rows)
        self.app.coding_personality_var.set(self.app._coding_option_label(self.app.coding_personality_options, personality_value))

        access_rows = CODING_ACCESS_OPTIONS.get(provider_key(profile), [("Default", "default")])
        access_value = preferences["access"]
        if access_value not in {value for _label, value in access_rows}:
            access_value = access_rows[0][1]
        self.app.coding_access_options = dict(access_rows)
        self.app.coding_access_var.set(self.app._coding_option_label(self.app.coding_access_options, access_value))

        if hasattr(self.app, "coding_model_combo"):
            self.app.coding_model_combo.configure(values=list(self.app.coding_model_options), state="readonly")
            effort_state = "readonly" if len(self.app.coding_effort_options) > 1 else "disabled"
            self.app.coding_effort_combo.configure(values=list(self.app.coding_effort_options), state=effort_state)
            personality_state = "readonly" if len(self.app.coding_personality_options) > 1 else "disabled"
            self.app.coding_personality_combo.configure(values=list(self.app.coding_personality_options), state=personality_state)
            self.app.coding_access_combo.configure(values=list(self.app.coding_access_options), state="readonly")

    def coding_control_values(self) -> dict[str, str]:
        return {
            "model": self.app.coding_model_options.get(self.app.coding_model_var.get(), ""),
            "effort": self.app.coding_effort_options.get(self.app.coding_effort_var.get(), ""),
            "personality": self.app.coding_personality_options.get(self.app.coding_personality_var.get(), ""),
            "access": self.app.coding_access_options.get(self.app.coding_access_var.get(), "default"),
        }

    def on_coding_control_changed(self, key: str) -> None:
        profile = self.app.coding_selected_profile()
        if profile is None or key not in {"model", "effort", "access", "personality"}:
            return
        values = self.app.coding_control_values()
        self.app._save_coding_control_preference(profile, key, values[key])
        if key == "model":
            self.app._sync_coding_controls()
        self.app.coding_composer_status.configure(text="Applies next turn")

    def _set_coding_control_value(self, key: str, value: str) -> bool:
        profile = self.app.coding_selected_profile()
        if profile is None:
            return False
        values_by_key = {
            "access": {option for _label, option in CODING_ACCESS_OPTIONS.get(provider_key(profile), [])},
            "personality": {option for _label, option in CODING_PERSONALITY_OPTIONS.get(provider_key(profile), [])},
        }
        if value not in values_by_key.get(key, set()):
            return False
        self.app._save_coding_control_preference(profile, key, value)
        self.app._sync_coding_controls()
        return True

    def _effective_codex_model(self, profile: dict, controls: dict[str, str]) -> str:
        if controls.get("model"):
            return controls["model"]
        defaults = self.app._coding_defaults_cache.get(profile_id(profile)) or read_coding_profile_defaults(profile)
        if defaults.get("model"):
            return str(defaults["model"])
        for _label, value in self.app._coding_model_rows(profile):
            if value:
                return value
        return "gpt-5.5"

    def _codex_plan_collaboration_mode(self, profile: dict, controls: dict[str, str]) -> dict:
        settings: dict[str, object] = {"model": self.app._effective_codex_model(profile, controls)}
        if controls.get("effort"):
            settings["reasoning_effort"] = controls["effort"]
        return {"mode": "plan", "settings": settings}

    def visible_profiles(self) -> list[dict]:
        term = self.app.search_var.get().strip().lower()
        rows = []
        if not term:
            rows = list(self.app.profiles)
        else:
            for profile in self.app.profiles:
                haystack = f"{profile.get('name', '')} {provider_label(profile)} {profile.get('codexHome', '')}".lower()
                if term in haystack:
                    rows.append(profile)
        return self.app.sorted_profiles(rows)

    def sorted_profiles(self, profiles: list[dict]) -> list[dict]:
        mode = self.app.sort_var.get() if hasattr(self.app, "sort_var") else "Manual"
        if mode not in SORT_CHOICES or mode == "Manual":
            return profiles
        indexed = {profile_id(profile): index for index, profile in enumerate(self.app.profiles)}

        def left_value(raw: object) -> float:
            value = percent_left(raw)
            return -1.0 if value is None else value

        def refresh_value(raw: object) -> float:
            parsed = parse_iso_datetime(raw)
            return parsed.timestamp() if parsed else 0.0

        def key(profile: dict) -> tuple:
            pid = profile_id(profile)
            if mode == "Name":
                return (str(profile.get("name", "")).lower(), indexed.get(pid, 0))
            if mode == "Provider":
                return (provider_label(profile).lower(), str(profile.get("name", "")).lower(), indexed.get(pid, 0))
            if mode == "State":
                order = {"ready": 0, "not_ready": 1, "login": 2, "error": 3}
                return (order.get(self.app.profile_state(profile), 9), str(profile.get("name", "")).lower(), indexed.get(pid, 0))
            if mode in {"5h left", "Session left"}:
                return (-left_value(profile.get("shortLimitUsedPercent")), str(profile.get("name", "")).lower(), indexed.get(pid, 0))
            if mode == "Weekly left":
                return (-left_value(profile.get("weeklyLimitUsedPercent")), str(profile.get("name", "")).lower(), indexed.get(pid, 0))
            if mode == "Last refresh":
                return (-refresh_value(profile.get("lastLimitsRefreshUtc")), str(profile.get("name", "")).lower(), indexed.get(pid, 0))
            return (indexed.get(pid, 0),)

        return sorted(profiles, key=key)

    def profile_state(self, profile: dict) -> str:
        pid = profile_id(profile)
        cached = self.app._profile_state_cache.get(pid)
        if cached:
            return cached
        state = effective_state(profile)
        self.app._profile_state_cache[pid] = state
        return state

    def selected_profile_obj(self) -> dict | None:
        if self.app.selected_profile == "all":
            return None
        for profile in self.app.profiles:
            if profile_id(profile) == self.app.selected_profile:
                return profile
        return None

    def active_desktop_marker(self) -> dict:
        if not DESKTOP_ACTIVE_PROFILE_PATH.exists():
            return {}
        try:
            return json.loads(DESKTOP_ACTIVE_PROFILE_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {}

    def active_desktop_home(self) -> str:
        return str(self.app.active_desktop_marker().get("codexHome") or "")

    def select_profile(self, pid: str) -> None:
        self.app._profile_state_cache = {}
        self.app.selected_profile = pid
        self.app.status_var.set("Selected all profiles" if pid == "all" else f"Selected {self.app.selected_profile_obj().get('name', pid) if self.app.selected_profile_obj() else pid}")
        self.app._update_buttons()
        self.app._refresh_account_selection_styles()
        self.app._render_details()

    def reload_profiles(self) -> None:
        self.app.profiles = load_profiles()
        if self.app.selected_profile != "all" and self.app.selected_profile not in {profile_id(profile) for profile in self.app.profiles}:
            self.app.selected_profile = "all"
        self.app.log("Reloaded profiles.")
        self.app.render()

    def next_account_index(self) -> int:
        highest = 0
        for profile in self.app.profiles:
            match = re.search(r"account-(\d+)$", str(profile.get("codexHome", "")), re.IGNORECASE)
            if match:
                highest = max(highest, int(match.group(1)))
        if DEFAULT_ACCOUNTS_ROOT.exists():
            for path in DEFAULT_ACCOUNTS_ROOT.iterdir():
                if path.is_dir():
                    match = re.match(r"account-(\d+)$", path.name, re.IGNORECASE)
                    if match:
                        highest = max(highest, int(match.group(1)))
        return highest + 1

    def next_account_home(self) -> Path:
        index = self.app.next_account_index()
        while True:
            candidate = DEFAULT_ACCOUNTS_ROOT / f"account-{index}"
            if not candidate.exists() and str(candidate) not in {str(profile.get("codexHome", "")) for profile in self.app.profiles}:
                return candidate
            index += 1

    def profile_index(self, profile: dict | None) -> int:
        if profile is None:
            return -1
        target = profile_id(profile)
        for index, item in enumerate(self.app.profiles):
            if item is profile or profile_id(item) == target:
                return index
        return -1

    def on_sort_changed(self) -> None:
        mode = self.app.sort_var.get()
        if mode not in SORT_CHOICES:
            self.app.sort_var.set("Manual")
            return
        self.app.settings["sortMode"] = mode
        save_settings(self.app.settings)
        self.app.render()

    def on_card_template_changed(self) -> None:
        template = self.app.card_template_var.get()
        if template not in CARD_TEMPLATE_CHOICES:
            self.app.card_template_var.set("Balanced")
            return
        self.app.settings["cardTemplate"] = template
        save_settings(self.app.settings)
        self.app._render_accounts()

    def edit_selected_account(self) -> None:
        profile = self.app.selected_required()
        if profile is not None:
            self.app.add_account_dialog(profile)

    def rename_selected_account(self) -> None:
        profile = self.app.selected_required()
        if profile is None:
            return
        dialog = tk.Toplevel(self.app)
        dialog.title("Rename Account")
        dialog.transient(self.app)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=PANEL)
        name_var = tk.StringVar(value=str(profile.get("name", "Account")))
        body = tk.Frame(dialog, bg=PANEL, padx=16, pady=14)
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        tk.Label(body, text="Rename Account", bg=PANEL, fg=INK, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 10))
        entry = tk.Entry(body, textvariable=name_var, bg=PANEL_ALT, fg=INK, insertbackground=INK, relief="solid", bd=1, font=("Segoe UI", 10), width=42)
        entry.grid(row=1, column=0, sticky="ew", ipady=5)
        buttons = tk.Frame(body, bg=PANEL)
        buttons.grid(row=2, column=0, sticky="e", pady=(12, 0))

        def save() -> None:
            name = name_var.get().strip()
            if not name:
                messagebox.showerror("Missing name", "Enter an account name.", parent=dialog)
                return
            profile["name"] = name
            self.app.save_current_profiles()
            self.app.log(f"Renamed account to {name}.")
            dialog.destroy()
            self.app.render()

        tk.Button(buttons, text="Cancel", command=dialog.destroy, bg=PANEL, fg=INK, relief="solid", bd=1, padx=12, pady=5).pack(side="left", padx=4)
        tk.Button(buttons, text="Save", command=save, bg=PRIMARY, fg="white", relief="flat", padx=12, pady=6).pack(side="left", padx=4)
        entry.focus_set()
        entry.select_range(0, "end")
        dialog.bind("<Return>", lambda _event: save())
        dialog.update_idletasks()
        x = self.app.winfo_rootx() + max(40, (self.app.winfo_width() - dialog.winfo_width()) // 2)
        y = self.app.winfo_rooty() + max(40, (self.app.winfo_height() - dialog.winfo_height()) // 3)
        dialog.geometry(f"+{x}+{y}")

    def delete_selected_account(self) -> None:
        profile = self.app.selected_required()
        if profile is None:
            return
        index = self.app.profile_index(profile)
        if index < 0:
            return
        name = str(profile.get("name", "Account"))
        ok = messagebox.askokcancel(
            "Delete account",
            f"Remove {name} from AI Account Hub?\n\nThis only removes the profile entry. It does not delete the account home folder or revoke login tokens.",
            icon="warning",
        )
        if not ok:
            return
        self.app.profiles.pop(index)
        self.app.selected_profile = "all"
        self.app.save_current_profiles()
        self.app.log(f"Deleted profile entry for {name}.")
        self.app.render()

    def move_selected_account(self, delta: int) -> None:
        profile = self.app.selected_required()
        if profile is None:
            return
        if self.app.sort_var.get() != "Manual":
            messagebox.showinfo("Manual sort required", "Switch Sort to Manual before moving accounts.")
            return
        index = self.app.profile_index(profile)
        target = index + delta
        if index < 0 or target < 0 or target >= len(self.app.profiles):
            return
        self.app.profiles[index], self.app.profiles[target] = self.app.profiles[target], self.app.profiles[index]
        self.app.save_current_profiles()
        self.app.log(f"Moved {profile.get('name', 'Account')} {'up' if delta < 0 else 'down'}.")
        self.app.render()

    def add_account_dialog(self, existing: dict | None = None) -> None:
        index = self.app.next_account_index()
        home = self.app.next_account_home()
        editing = existing is not None
        existing_index = self.app.profile_index(existing)
        existing_provider = provider_key(existing) if existing is not None else "codex"
        provider_labels = {value: label for label, value in PROVIDER_CHOICES}
        dialog = tk.Toplevel(self.app)
        dialog.title("Edit Account" if editing else "Add Account")
        dialog.transient(self.app)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=PANEL)

        name_var = tk.StringVar(value=str(existing.get("name", "Account")) if existing else f"Account {index}")
        provider_label_var = tk.StringVar(value=provider_labels.get(existing_provider, PROVIDER_CHOICES[0][0]) if existing else PROVIDER_CHOICES[0][0])
        home_var = tk.StringVar(value=str(existing.get("codexHome", "")) if existing else str(home))
        workspace_var = tk.StringVar(value=str(existing.get("workspace", _hub_globals["DEFAULT_WORKSPACE"])) if existing else str(_hub_globals["DEFAULT_WORKSPACE"]))
        browser_command_var = tk.StringVar(value=str(existing.get("browserCommand", "")) if existing else "")
        browser_profile_mode_var = tk.BooleanVar(value=browser_profile_mode(existing or {}) == "isolated")
        browser_profile_dir_var = tk.StringVar(value=str(existing.get("browserProfileDir", "")) if existing else "")
        existing_links = existing.get("onlineLinks", []) if existing else []
        online_links_initial = existing_links if isinstance(existing_links, str) else serialize_online_links_text(existing_links)

        body = tk.Frame(dialog, bg=PANEL, padx=16, pady=14)
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(1, weight=1)

        tk.Label(body, text="Edit Account" if editing else "Add Account", bg=PANEL, fg=INK, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        def field(row: int, label: str, widget: tk.Widget) -> None:
            tk.Label(body, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 8), width=12, anchor="w").grid(row=row, column=0, sticky="w", pady=5)
            widget.grid(row=row, column=1, sticky="ew", pady=5, ipady=4)

        name_entry = tk.Entry(body, textvariable=name_var, bg=PANEL_ALT, fg=INK, insertbackground=INK, relief="solid", bd=1, font=("Segoe UI", 9))
        field(1, "Name", name_entry)

        provider_combo = ttk.Combobox(body, textvariable=provider_label_var, values=[label for label, _value in PROVIDER_CHOICES], state="readonly", width=34, style="Hub.TCombobox")
        field(2, "Provider", provider_combo)

        home_entry = tk.Entry(body, textvariable=home_var, bg=PANEL_ALT, fg=INK, insertbackground=INK, relief="solid", bd=1, font=("Segoe UI", 9), width=48)
        field(3, "Home", home_entry)

        workspace_entry = tk.Entry(body, textvariable=workspace_var, bg=PANEL_ALT, fg=INK, insertbackground=INK, relief="solid", bd=1, font=("Segoe UI", 9), width=48)
        field(4, "Workspace", workspace_entry)

        browser_mode_check = tk.Checkbutton(
            body,
            text="Use isolated browser profile for website cookies",
            variable=browser_profile_mode_var,
            bg=PANEL,
            fg=INK,
            selectcolor=PANEL_ALT,
            activebackground=PANEL,
            activeforeground=INK,
            anchor="w",
            font=("Segoe UI", 9),
        )
        browser_mode_check.grid(row=5, column=1, sticky="w", pady=5)

        browser_profile_entry = tk.Entry(body, textvariable=browser_profile_dir_var, bg=PANEL_ALT, fg=INK, insertbackground=INK, relief="solid", bd=1, font=("Segoe UI", 9), width=48)
        field(6, "Cookie profile", browser_profile_entry)

        browser_entry = tk.Entry(body, textvariable=browser_command_var, bg=PANEL_ALT, fg=INK, insertbackground=INK, relief="solid", bd=1, font=("Segoe UI", 9), width=48)
        field(7, "Browser cmd", browser_entry)

        online_links_text = tk.Text(body, height=4, width=48, bg=PANEL_ALT, fg=INK, insertbackground=INK, relief="solid", bd=1, font=("Segoe UI", 9), wrap="none")
        online_links_text.insert("1.0", online_links_initial)
        field(8, "Online links", online_links_text)

        tk.Label(body, text="Custom links: Label | https://example.com. Browser cmd overrides the cookie profile and can include {url}.", bg=PANEL, fg=MUTED, font=("Segoe UI", 8), anchor="w").grid(row=9, column=1, sticky="w", pady=(0, 4))

        buttons = tk.Frame(body, bg=PANEL)
        buttons.grid(row=10, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def selected_provider() -> str:
            return dict(PROVIDER_CHOICES).get(provider_label_var.get().strip(), "codex")

        def provider_account_home(provider: str, primary: Path) -> Path:
            other_profiles = [
                item
                for item in self.app.profiles
                if item is not existing and provider_key(item) == provider
            ]
            if not other_profiles:
                return primary
            return HUB_ACCOUNTS_ROOT / provider / f"account-{index}"

        def provider_defaults(_event: tk.Event | None = None) -> None:
            provider = selected_provider()
            if provider == "claude":
                if name_var.get().strip().startswith("Account "):
                    name_var.set("Claude Code")
                home_var.set(str(provider_account_home("claude", _hub_globals["CLAUDE_CLI_HOME"])))
            elif provider == "cursor":
                if name_var.get().strip().startswith("Account ") or name_var.get().strip() in {"Claude Code", "Antigravity", "Antigravity 2.0", "Gemini"}:
                    name_var.set("Cursor")
                home_var.set(str(CURSOR_ROAMING_HOME))
            elif provider == "antigravity":
                if name_var.get().strip().startswith("Account ") or name_var.get().strip() in {"Claude Code", "Cursor", "Gemini"}:
                    name_var.set("Antigravity")
                home_var.set(str(ANTIGRAVITY_ROAMING_HOME))
            elif provider == "codex":
                if home_var.get().strip() in {str(_hub_globals["CLAUDE_CLI_HOME"]), str(CLAUDE_ROAMING_HOME), str(CURSOR_ROAMING_HOME), str(ANTIGRAVITY_ROAMING_HOME)}:
                    home_var.set(str(home))
                if name_var.get().strip() in {"Claude Code", "Cursor", "Antigravity", "Antigravity 2.0", "Gemini"}:
                    name_var.set(f"Account {index}")
            else:
                if name_var.get().strip().startswith("Account ") or name_var.get().strip() in {"Claude Code", "Cursor", "Antigravity", "Antigravity 2.0", "Gemini"}:
                    name_var.set(provider_label_var.get().strip())
                home_var.set(str(HUB_ACCOUNTS_ROOT / provider / f"account-{index}"))

        provider_combo.bind("<<ComboboxSelected>>", provider_defaults)

        def cancel() -> None:
            dialog.destroy()

        def create() -> None:
            name = name_var.get().strip()
            provider_label_text = provider_label_var.get().strip()
            provider = dict(PROVIDER_CHOICES).get(provider_label_text, "codex")
            codex_home = home_var.get().strip()
            workspace = workspace_var.get().strip()
            browser_command = browser_command_var.get().strip()
            browser_profile_mode_value = "isolated" if browser_profile_mode_var.get() else "system"
            browser_profile_dir = browser_profile_dir_var.get().strip()
            custom_link_text = online_links_text.get("1.0", "end").strip()
            raw_custom_link_lines = [line.strip() for line in custom_link_text.splitlines() if line.strip()]
            custom_links = parse_custom_online_links_text(custom_link_text)
            if not name:
                messagebox.showerror("Missing name", "Enter an account name.", parent=dialog)
                return
            if not codex_home:
                messagebox.showerror("Missing home", "Enter an account home folder.", parent=dialog)
                return
            if not workspace:
                messagebox.showerror("Missing workspace", "Enter a workspace folder.", parent=dialog)
                return
            if len(custom_links) != len(raw_custom_link_lines):
                messagebox.showerror("Invalid online links", "Use one custom link per line as Label | https://example.com.", parent=dialog)
                return

            payload = dict(existing) if existing is not None else {}
            payload.update(
                {
                    "name": name,
                    "provider": provider,
                    "codexHome": codex_home,
                    "workspace": workspace,
                    "browserCommand": browser_command,
                    "browserProfileMode": browser_profile_mode_value,
                    "browserProfileDir": browser_profile_dir,
                    "onlineLinks": custom_links,
                }
            )
            if provider == "claude":
                payload["claudeConfigDir"] = codex_home
            else:
                payload.pop("claudeConfigDir", None)
            payload.pop("geminiConfigDir", None)
            if provider == "codex":
                payload.pop("id", None)
            elif not str(payload.get("id") or "").strip():
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or provider
                payload["id"] = f"{provider}:{slug}:{dt.datetime.now().strftime('%Y%m%d%H%M%S')}"
            profile = normalize_profile(payload, len(self.app.profiles))
            try:
                ensure_profile_home(profile)
            except Exception as error:
                messagebox.showerror("Could not create account", str(error), parent=dialog)
                return

            if editing and existing_index >= 0:
                self.app.profiles[existing_index] = profile
            else:
                self.app.profiles.append(profile)
            self.app.selected_profile = profile_id(profile)
            self.app.search_var.set("")
            self.app.save_current_profiles()
            dialog.destroy()
            self.app.log(f"{'Updated' if editing else 'Added'} {provider_label(profile)} account: {name}.")
            self.app.render()

        tk.Button(buttons, text="Cancel", command=cancel, bg=PANEL, fg=INK, relief="solid", bd=1, padx=12, pady=5).pack(side="left", padx=4)
        tk.Button(buttons, text="Save" if editing else "Create", command=create, bg=PRIMARY, fg="white", relief="flat", padx=12, pady=6).pack(side="left", padx=4)

        name_entry.focus_set()
        if editing:
            name_entry.select_range(0, "end")
        dialog.update_idletasks()
        x = self.app.winfo_rootx() + max(40, (self.app.winfo_width() - dialog.winfo_width()) // 2)
        y = self.app.winfo_rooty() + max(40, (self.app.winfo_height() - dialog.winfo_height()) // 3)
        dialog.geometry(f"+{x}+{y}")
