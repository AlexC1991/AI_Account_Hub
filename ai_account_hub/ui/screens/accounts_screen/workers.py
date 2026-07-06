"""Background QThread workers for the Accounts screen: full refresh and per-account actions."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ai_account_hub import data

class RefreshWorker(QThread):
    """Runs blocking provider refreshes off the UI thread."""

    progress = Signal(str)          # log line
    one_done = Signal(str, bool)    # profile id, ok
    finished_all = Signal()

    def __init__(self, profiles: list[dict], reason: str = "refresh-all") -> None:
        super().__init__()
        self._profiles = profiles
        self._reason = reason

    def run(self) -> None:
        for profile in self._profiles:
            name = str(profile.get("name", "Account"))
            self.progress.emit(f"Refreshing {name}…")
            result = data.refresh_one(profile, reason=self._reason)
            ok = bool(result.get("ok"))
            self.one_done.emit(data.profile_id(profile), ok)
            self.progress.emit(f"{'Refreshed' if ok else 'Could not refresh'} {name}"
                               + ("" if ok else f": {result.get('error')}"))
        data.save_profiles(self._profiles)
        self.finished_all.emit()



class ActionWorker(QThread):
    """Runs a blocking engine action (status/doctor/reset) off the UI thread."""

    done = Signal(bool, str)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            ok, message = self._fn()
        except Exception as error:
            ok, message = False, str(error)
        self.done.emit(bool(ok), str(message))


