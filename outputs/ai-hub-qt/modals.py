"""Add-profile and edit-profile modal dialogs (design 3d).

Full form dialogs (not the minimal input prompts): display name, provider
select that auto-fills a sensible plan default, and a read-only derived
profile path. Reuse the legacy normalize_profile / ensure_profile_home so a
new account is written exactly like the Tk app writes it.
"""

from __future__ import annotations

import datetime as _dt
import re

from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QVBoxLayout,
)

import data
import legacy_backend as L
from widgets import make_button

_PLAN_DEFAULTS = {"codex": "Plus", "claude": "Pro", "cursor": "Pro", "antigravity": "Pro"}
_PROVIDERS = [
    ("Codex", "codex", ""),
    ("Claude Code (paid)", "claude", "code"),
    ("Cursor", "cursor", ""),
    ("Antigravity", "antigravity", ""),
]

# Used For Testing Claude Account Switching
# The backend still understands this profile type, but it is deliberately not
# offered by Add Profile because it has no Claude Code/Coding capability.
_CLAUDE_DESKTOP_TEST_PROFILE = ("Claude Desktop (free)", "claude", "desktop")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "profile").lower()).strip("-") or "profile"


def _derive_path(provider: str, name: str, claude_type: str = "") -> str:
    if provider == "codex":
        root = L.DEFAULT_ACCOUNTS_ROOT
    elif provider == "claude" and claude_type == "desktop":
        root = L.HUB_ACCOUNTS_ROOT / "claude-desktop"
    else:
        root = L.HUB_ACCOUNTS_ROOT / provider
    return str(root / _slug(name))


class AddProfileDialog(QDialog):
    def __init__(self, parent, existing_count: int) -> None:
        super().__init__(parent)
        self._count = existing_count
        self.result_profile: dict | None = None
        self.setWindowTitle("Add profile")
        self.setMinimumWidth(440)
        self._build()

    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)
        title = QLabel("Add profile")
        title.setStyleSheet("font-size:16px;font-weight:700;")
        v.addWidget(title)
        v.addWidget(_sub("Register a coding account or a switchable Desktop-only account."))

        form = QFormLayout()
        form.setSpacing(8)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Codex Client Work")
        self.name.textChanged.connect(self._refresh)
        form.addRow("Display name", self.name)
        self.provider = QComboBox()
        for label, provider, claude_type in _PROVIDERS:
            self.provider.addItem(label, (provider, claude_type))
        self.provider.currentIndexChanged.connect(self._provider_changed)
        form.addRow("Provider", self.provider)
        self.plan = QLineEdit(_PLAN_DEFAULTS["codex"])
        form.addRow("Plan", self.plan)
        self.email = QLineEdit()
        self.email.setPlaceholderText("Optional label, e.g. name@example.com")
        form.addRow("Account email", self.email)
        self.path = QLineEdit()
        self.path.setStyleSheet("font-family:Consolas,monospace;font-size:11px;")
        form.addRow("Profile path", self.path)
        self.workspace = QLineEdit(str(L.DEFAULT_WORKSPACE))
        form.addRow("Workspace", self.workspace)
        self.browser_mode = QComboBox()
        self.browser_mode.addItem("Isolated account browser", "isolated")
        self.browser_mode.addItem("System browser", "system")
        self.browser_mode.addItem("Custom command", "custom")
        form.addRow("Online browser", self.browser_mode)
        v.addLayout(form)
        self.provider_note = _sub("")
        self.provider_note.setWordWrap(True)
        v.addWidget(self.provider_note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = make_button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        self.submit = make_button("Add profile", "primary")
        self.submit.clicked.connect(self._create)
        self.submit.setEnabled(False)
        buttons.addWidget(cancel)
        buttons.addWidget(self.submit)
        v.addLayout(buttons)
        self._provider_changed()

    def _provider_key(self) -> str:
        return str((self.provider.currentData() or ("codex", ""))[0])

    def _claude_type(self) -> str:
        value = self.provider.currentData() or ("codex", "")
        return str(value[1]) if self._provider_key() == "claude" else ""

    def _provider_changed(self) -> None:
        plan = "Free" if self._claude_type() == "desktop" else _PLAN_DEFAULTS.get(self._provider_key(), "Pro")
        self.plan.setText(plan)
        self.plan.setEnabled(self._claude_type() != "desktop")
        if self._provider_key() == "claude" and self._claude_type() == "code":
            self.provider_note.setText(
                "Paid Claude account: use Login for Claude Code, then Desktop Login "
                "for Claude Desktop. Both are one-time setup steps."
            )
        elif self._provider_key() == "claude":
            self.provider_note.setText(
                "Free Desktop-only account: supports Desktop switching, but not "
                "Claude Code, Coding, limits, Status, or Doctor."
            )
        else:
            self.provider_note.setText(
                "The Hub keeps this provider's profile state separate from other accounts."
            )
        self._refresh()

    def _refresh(self) -> None:
        self.path.setText(_derive_path(
            self._provider_key(),
            self.name.text().strip() or "profile",
            self._claude_type(),
        ))
        self.submit.setEnabled(bool(self.name.text().strip()))

    def _create(self) -> None:
        name = self.name.text().strip()
        if not name:
            return
        provider = self._provider_key()
        path = self.path.text().strip()
        payload = {
            "name": name, "provider": provider, "codexHome": path,
            "workspace": self.workspace.text().strip() or str(L.DEFAULT_WORKSPACE),
            "accountPlan": (
                "Free" if self._claude_type() == "desktop"
                else self.plan.text().strip() or _PLAN_DEFAULTS.get(provider, "Pro")
            ),
            "accountEmail": self.email.text().strip(),
            "browserProfileMode": str(self.browser_mode.currentData() or "isolated"),
            "onlineLinks": [],
        }
        if provider == "claude":
            payload["claudeConfigDir"] = path
            payload["claudeProfileType"] = self._claude_type() or "code"
        if provider != "codex":
            payload["id"] = f"{provider}:{_slug(name)}:{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}"
        profile = L.normalize_profile(payload, self._count)
        try:
            L.ensure_profile_home(profile)
        except Exception:
            pass
        self.result_profile = profile
        self.accept()


class EditProfileDialog(QDialog):
    def __init__(self, parent, profile: dict) -> None:
        super().__init__(parent)
        self._profile = profile
        self.setWindowTitle("Edit profile")
        self.setMinimumWidth(440)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)
        title = QLabel("Edit profile")
        title.setStyleSheet("font-size:16px;font-weight:700;")
        v.addWidget(title)
        form = QFormLayout()
        provider = data.provider_key(profile)
        provider_label = QLabel(data.provider_label(profile))
        self.name = QLineEdit(str(profile.get("name", "")))
        self.plan = QLineEdit(str(profile.get("accountPlan", "")))
        self.email = QLineEdit(str(profile.get("accountEmail", "")))
        self.workspace = QLineEdit(str(profile.get("workspace", "")))
        self.home = QLineEdit(
            str(profile.get("claudeConfigDir") or profile.get("codexHome") or "")
        )
        self.home.setStyleSheet("font-family:Consolas,monospace;font-size:11px;")
        self.browser_mode = QComboBox()
        self.browser_mode.addItem("Isolated account browser", "isolated")
        self.browser_mode.addItem("System browser", "system")
        self.browser_mode.addItem("Custom command", "custom")
        mode = L.browser_profile_mode(profile)
        index = self.browser_mode.findData(mode)
        if index >= 0:
            self.browser_mode.setCurrentIndex(index)
        self.browser_command = QLineEdit(str(profile.get("browserCommand") or ""))
        self.browser_command.setPlaceholderText("Optional browser executable/command")
        self.links = QPlainTextEdit()
        self.links.setPlaceholderText("Label | https://example.com (one per line)")
        self.links.setPlainText(L.serialize_online_links_text(profile.get("onlineLinks")))
        self.links.setMaximumHeight(90)
        self.antigravity_timeout = QLineEdit(str(profile.get("antigravityPrintTimeout") or "5m"))
        form.addRow("Provider", provider_label)
        form.addRow("Display name", self.name)
        form.addRow("Plan", self.plan)
        form.addRow("Account email", self.email)
        self.claude_type = None
        if provider == "claude":
            desktop_test = L.claude_desktop_only(profile)
            access = QLabel(
                "Claude Desktop test profile" if desktop_test else "Claude Code (paid)"
            )
            form.addRow("Claude access", access)
            if desktop_test:
                self.plan.setText("Free")
                self.plan.setEnabled(False)
        form.addRow("Workspace", self.workspace)
        form.addRow("Profile path", self.home)
        form.addRow("Online browser", self.browser_mode)
        form.addRow("Browser command", self.browser_command)
        form.addRow("Custom links", self.links)
        if provider == "antigravity":
            form.addRow("Print timeout", self.antigravity_timeout)
        v.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = make_button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        save = make_button("Save", "primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        v.addLayout(buttons)

    def _save(self) -> None:
        self._profile["name"] = self.name.text().strip() or self._profile.get("name")
        self._profile["accountPlan"] = (
            "Free"
            if self.claude_type is not None and self.claude_type.currentData() == "desktop"
            else self.plan.text().strip()
        )
        self._profile["accountEmail"] = self.email.text().strip()
        if self.claude_type is not None:
            previous = L.claude_profile_type(self._profile)
            current = str(self.claude_type.currentData() or "code")
            self._profile["claudeProfileType"] = current
            if previous != current:
                self._profile["lastLimitsError"] = ""
                self._profile["lastUsageError"] = ""
        if self.workspace.text().strip():
            self._profile["workspace"] = self.workspace.text().strip()
        home = self.home.text().strip()
        if home:
            self._profile["codexHome"] = home
            if data.provider_key(self._profile) == "claude":
                self._profile["claudeConfigDir"] = home
        self._profile["browserProfileMode"] = str(self.browser_mode.currentData() or "isolated")
        self._profile["browserCommand"] = self.browser_command.text().strip()
        self._profile["onlineLinks"] = L.parse_custom_online_links_text(self.links.toPlainText())
        if data.provider_key(self._profile) == "antigravity":
            self._profile["antigravityPrintTimeout"] = L.normalize_antigravity_print_timeout(
                self.antigravity_timeout.text()
            )
        self.accept()

def _sub(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("faint")
    return lab
