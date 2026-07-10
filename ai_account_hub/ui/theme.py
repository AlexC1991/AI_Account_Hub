"""Theme manager: turns a token dict into an application-wide QSS stylesheet.

The whole point of the Qt port: rounded corners, accent gradients, hover
states, and soft borders are all expressed once here as QSS and applied to the
QApplication. Switching theme re-applies the stylesheet in place -- widgets
restyle without being destroyed and rebuilt (the core flow requirement).

Styling variants are selected with Qt dynamic properties, e.g. a button with
``btn.setProperty("variant", "primary")`` picks up the gradient rule below.
After changing a dynamic property at runtime, call ``ThemeManager.repolish(w)``.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ai_account_hub.ui.tokens import THEMES, DEFAULT_THEME, accent_gradient, soft, light_variant


class ThemeManager(QObject):
    changed = Signal(str)  # emits theme name

    def __init__(self, app, theme_name: str = DEFAULT_THEME, mode: str = "dark") -> None:
        super().__init__()
        self._app = app
        self._name = theme_name if theme_name in THEMES else DEFAULT_THEME
        self._mode = mode if mode in ("dark", "light") else "dark"
        self._tokens = self._compute()
        self._reapply()

    def _compute(self) -> dict[str, str]:
        base = THEMES[self._name]
        return light_variant(self._name, base) if self._mode == "light" else base

    @property
    def name(self) -> str:
        return self._name

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def tokens(self) -> dict[str, str]:
        return self._tokens

    def apply(self, theme_name: str) -> None:
        if theme_name not in THEMES:
            return
        self._name = theme_name
        self._reapply()

    def set_mode(self, mode: str) -> None:
        if mode not in ("dark", "light") or mode == self._mode:
            return
        self._mode = mode
        self._reapply()

    def _reapply(self) -> None:
        self._tokens = self._compute()
        self._app.setStyleSheet(build_qss(self._tokens))
        # Push tokens to custom-painted widgets (AccentButton) that don't read QSS.
        from ai_account_hub.ui import widgets
        widgets.set_active_tokens(self._tokens)
        self.changed.emit(self._name)

    @staticmethod
    def repolish(widget) -> None:
        """Re-evaluate QSS for a widget after a dynamic property changed."""
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def build_qss(t: dict[str, str]) -> str:
    grad = accent_gradient(t)
    accent_soft = soft(t["accent"], 0.16)
    accent_soft_hi = soft(t["accent"], 0.24)
    success_soft = soft(t["success"], 0.16)
    return f"""
    * {{
        font-family: "Segoe UI Variable", "Segoe UI", system-ui, sans-serif;
        color: {t['text']};
        outline: none;
    }}
    QWidget#root {{ background: {t['bg']}; }}
    QWidget {{ background: transparent; }}

    /* ---- Title bar ---- */
    QWidget#titlebar {{ background: {t['bg']}; border-bottom: 1px solid {t['border']}; }}
    QLabel#titlebarText {{ color: {t['text3']}; font-size: 12px; }}
    QMenuBar {{ background: transparent; color: {t['text2']}; border: none; font-size: 11px; spacing: 1px; }}
    QMenuBar::item {{ background: transparent; padding: 5px 8px; border-radius: 4px; }}
    QMenuBar::item:selected {{ background: {t['panelHover']}; color: {t['text']}; }}
    QMenu {{ background: {t['panel']}; color: {t['text']}; border: 1px solid {t['borderStrong']}; padding: 5px; }}
    QMenu::item {{ padding: 7px 26px 7px 10px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {accent_soft}; color: {t['text']}; }}
    QMenu::separator {{ height: 1px; background: {t['border']}; margin: 5px 8px; }}
    QPushButton#winBtn {{ background: transparent; border: none; border-radius: 0; color: {t['text2']}; font-size: 13px; }}
    QPushButton#winBtn:hover {{ background: {t['panelHover']}; }}
    QPushButton#winClose:hover {{ background: #e81123; color: #ffffff; }}

    /* ---- App header ---- */
    QWidget#appHeader {{ background: {t['bg']}; border-bottom: 1px solid {t['border']}; }}
    QLabel#appTitle {{ font-size: 15px; font-weight: 600; color: {t['text']}; }}
    QLabel#appSubtitle {{ font-size: 11px; color: {t['text3']}; }}
    QFrame#logoTile {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 10px; }}

    /* segmented Coding|Accounts pill */
    QFrame#segTabs {{ background: {t['panel2']}; border-radius: 8px; }}
    QPushButton#segTab {{ background: transparent; border: none; border-radius: 6px; padding: 6px 18px;
        color: {t['text2']}; font-size: 12px; font-weight: 600; }}
    QPushButton#segTab:hover {{ color: {t['text']}; }}
    QPushButton#segTab[active="true"] {{ background: {grad}; color: {t['accentText']}; }}

    /* ---- Buttons ---- */
    /* Menu buttons render their own "⌄" glyph, so hide Qt's native arrow to
       avoid a doubled chevron. */
    QPushButton::menu-indicator {{ image: none; width: 0px; }}
    QPushButton[variant="ghost"] {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 7px;
        padding: 7px 12px; color: {t['text2']}; font-size: 12px; font-weight: 600; }}
    QPushButton[variant="ghost"]:hover {{ background: {t['panelHover']}; border-color: {t['borderStrong']}; color: {t['text']}; }}
    QPushButton[variant="ghost"]:disabled {{ color: {t['text3']}; }}

    /* Primary CTA. NOTE: Qt does not reliably paint a QPushButton *fill* from a
       [variant] stylesheet rule (only the border + text render), which left
       filled CTAs invisible on themes whose accentText is dark. So primary is
       styled as an accent outline + accent text (both always render); hover adds
       the soft accent wash. This reads as the primary action in every theme. */
    QPushButton[variant="primary"] {{ background: {accent_soft}; border: 1px solid {t['accent']}; border-radius: 7px;
        padding: 7px 14px; color: {t['accent']}; font-size: 12px; font-weight: 700; }}
    QPushButton[variant="primary"]:hover {{ background: {accent_soft_hi}; }}
    QPushButton[variant="primary"]:disabled {{ color: {soft(t['accent'], 0.45)}; border-color: {soft(t['accent'], 0.45)}; }}

    QPushButton[variant="success"] {{ background: {success_soft}; border: 1px solid {t['success']}; border-radius: 7px;
        padding: 7px 12px; color: {t['success']}; font-size: 12px; font-weight: 700; }}

    QPushButton[variant="danger"] {{ background: {t['panel2']}; border: 1px solid {soft(t['danger'], 0.5)}; border-radius: 7px;
        padding: 7px 12px; color: {t['danger']}; font-size: 12px; font-weight: 600; }}
    QPushButton[variant="danger"]:hover {{ background: {soft(t['danger'], 0.16)}; }}

    QPushButton[variant="dim"] {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 7px;
        padding: 7px 12px; color: {t['text3']}; font-size: 12px; font-weight: 600; }}
    QPushButton[variant="dim"]:hover {{ background: {t['panelHover']}; }}

    /* auto toggle pill */
    QPushButton#autoToggle {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 7px;
        padding: 7px 12px; color: {t['text2']}; font-size: 12px; font-weight: 600; }}
    QPushButton#autoToggle[on="true"] {{ background: {success_soft}; border-color: {soft(t['success'], 0.5)}; color: {t['success']}; }}

    /* ---- Cards / panels ---- */
    QFrame#card {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 9px; }}
    QFrame#card[selected="true"] {{ background: {accent_soft}; border-color: {t['accent']}; }}
    QFrame#panel {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 10px; }}
    QWidget#trayPopup {{ background: {t['panel']}; border: 1px solid {t['borderStrong']}; border-radius: 9px; }}
    QFrame#trayHeader {{ background: {t['panel']}; border-bottom: 1px solid {t['border']}; }}
    QFrame#trayFooter {{ background: {t['panel']}; border-top: 1px solid {t['border']}; }}
    QDialog#traySettingsDialog {{ background: {t['panel']}; }}
    QDialog#notificationSettingsDialog {{ background: {t['panel']}; }}
    QScrollArea#trayVisibilityScroll {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 7px; }}
    QWidget#trayVisibilityHost {{ background: {t['panel2']}; }}
    QLabel#dialogTitle {{ color: {t['text']}; font-size: 16px; font-weight: 700; }}
    QLabel#trayTitle {{ color: {t['text']}; font-size: 13px; font-weight: 700; }}
    QLabel#trayHeroName {{ color: {t['text']}; font-size: 14px; font-weight: 700; }}
    QLabel#trayAccountName {{ color: {t['text']}; font-size: 12px; font-weight: 600; }}
    QLabel#trayMetricValue {{ color: {t['text2']}; font-size: 11px; font-weight: 700; }}
    QPushButton#trayAccountRow {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 7px; padding: 0; text-align: left; }}
    QPushButton#trayAccountRow:hover {{ background: {t['panelHover']}; border-color: {t['borderStrong']}; }}
    QFrame#railDivider {{ background: {t['border']}; }}
    QLabel#sectionLabel {{ color: {t['text3']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
    QLabel#muted {{ color: {t['text2']}; font-size: 11px; }}
    QLabel#faint {{ color: {t['text3']}; font-size: 10px; }}
    QLabel#bigNumber {{ color: {t['text']}; font-size: 22px; font-weight: 600; }}

    /* status pills */
    QLabel[pill="ready"] {{ background: {success_soft}; color: {t['success']}; border-radius: 9px; padding: 2px 9px; font-size: 10px; font-weight: 700; }}
    QLabel[pill="warn"] {{ background: {soft(t['warn'], 0.16)}; color: {t['warn']}; border-radius: 9px; padding: 2px 9px; font-size: 10px; font-weight: 700; }}
    QLabel[pill="error"] {{ background: {soft(t['danger'], 0.16)}; color: {t['danger']}; border-radius: 9px; padding: 2px 9px; font-size: 10px; font-weight: 700; }}
    QLabel[pill="idle"] {{ background: {t['panelHover']}; color: {t['text2']}; border-radius: 9px; padding: 2px 9px; font-size: 10px; font-weight: 700; }}
    QLabel[pill="inuse"] {{ background: {accent_soft}; color: {t['accent']}; border-radius: 9px; padding: 2px 9px; font-size: 10px; font-weight: 700; }}

    /* ---- Inputs ---- */
    QLineEdit, QPlainTextEdit, QTextEdit {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 8px;
        padding: 8px 10px; color: {t['text']}; selection-background-color: {accent_soft_hi}; }}
    QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {t['accent']}; }}
    QComboBox {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 7px; padding: 6px 10px; color: {t['text']}; }}
    QComboBox:hover {{ border-color: {t['borderStrong']}; }}
    QComboBox QAbstractItemView {{ background: {t['panel']}; border: 1px solid {t['border']}; selection-background-color: {accent_soft}; }}
    QCheckBox {{ color: {t['text2']}; spacing: 9px; padding: 2px 0; }}
    QCheckBox:hover {{ color: {t['text']}; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; }}
    QCheckBox[visibilityProvider="true"] {{ color: {t['text']}; font-weight: 700; padding-top: 3px; }}
    QCheckBox[visibilityAccount="true"] {{ color: {t['text2']}; font-size: 11px; }}
    QCheckBox[visibilityAccount="true"]:disabled {{ color: {t['text3']}; }}

    /* ---- Account list cards (selectable) ---- */
    QFrame#accountCard {{ background: {t['panel2']}; border: 1px solid {t['border']}; border-radius: 10px; }}
    QFrame#accountCard:hover {{ border-color: {t['borderStrong']}; }}
    /* Selected rules come AFTER :hover so a selected card keeps its accent
       border while hovered (otherwise the selection flickered away on hover). */
    QFrame#accountCard[selected="true"] {{ background: {accent_soft}; border-color: {t['accent']}; }}
    QFrame#accountCard[selected="true"]:hover {{ background: {accent_soft}; border-color: {t['accent']}; }}

    /* ---- Scrollbars ---- */
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {t['borderStrong']}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {t['text3']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ height: 0; }}
    QScrollArea {{ border: none; background: transparent; }}

    /* ---- Calendar / nav helpers ---- */
    QPushButton#chevron {{ background: {t['panel']}; border: 1px solid {t['border']}; border-radius: 7px; color: {t['text2']}; font-size: 14px; padding: 4px 10px; }}
    QPushButton#chevron:hover {{ background: {t['panelHover']}; }}
    QPushButton#todayBtn {{ background: {accent_soft}; border: 1px solid {t['accent']}; border-radius: 7px; color: {t['accent']}; font-weight: 600; padding: 5px 12px; }}

    /* coding rail nav rows (flat, borderless) */
    QPushButton#navRow {{ background: transparent; border: none; border-radius: 8px; text-align: left; padding: 9px 10px; color: {t['text']}; font-size: 12px; }}
    QPushButton#navRow:hover {{ background: {t['panelHover']}; }}
    QLabel#kbd {{ color: {t['text3']}; font-size: 10px; }}
    """
