"""Design tokens exposed to QML as the `theme` context property."""
from PySide6.QtCore import Property, QObject, Signal
from PySide6.QtGui import QColor, QFontDatabase
from .i18n import N_

ACCENTS = [
    (N_("Laranja"), "#FF7A2F"),
    (N_("Coral"), "#FF5A5F"),
    (N_("Azul"), "#3E91FF"),
    (N_("Índigo"), "#6C63FF"),
    (N_("Menta"), "#22C3A6"),
    (N_("Âmbar"), "#E8A33D"),
    (N_("Rosa"), "#F25C9A"),
    (N_("Grafite"), "#8E8E93"),
]

PALETTES = {
    "dark": {
        "bg": "#0C0D10", "bgAlt": "#111216", "surface": "#17181D", "surface2": "#1F2026",
        "surface3": "#2A2B32", "text": "#F5F5F7", "textDim": "#A3A3AD", "textFaint": "#6A6A74",
        "stroke": "#14FFFFFF", "strokeStrong": "#26FFFFFF", "glass": "#B3181A20",
        "glassStrong": "#E61A1B21", "shadow": "#99000000", "inverse": "#F5F5F7", "inverseText": "#111214",
        "scrim": "#CC0C0D10",
    },
    "light": {
        "bg": "#F1EEE9", "bgAlt": "#EAE6E0", "surface": "#FBFAF8", "surface2": "#E8E4DE",
        "surface3": "#DCD7CF", "text": "#1B1A18", "textDim": "#6D6962", "textFaint": "#9E9A92",
        "stroke": "#14000000", "strokeStrong": "#24000000", "glass": "#C7FBFAF8",
        "glassStrong": "#F2FBFAF8", "shadow": "#33000000", "inverse": "#262523", "inverseText": "#F7F5F2",
        "scrim": "#99F1EEE9",
    },
}


def _reduce_motion() -> bool:
    import os
    forced = os.environ.get("REFRATA_REDUCE_MOTION")
    if forced is not None:
        return forced == "1"
    try:
        import ctypes
        on = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION: the "Show animations in Windows" switch
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0):
            return not on.value
    except (AttributeError, OSError):
        pass
    return False


def _bundled_icon_font() -> str:
    """Fluent UI System Icons (MIT), cut down to the app's icons: Segoe Fluent Icons is Windows-only."""
    from .vlcpath import app_root
    fid = QFontDatabase.addApplicationFont(str(app_root() / "assets" / "fonts" / "FluentSystemIcons-Refrata.ttf"))
    fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    return fams[0] if fams else "Sans Serif"


class Theme(QObject):
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._s = settings
        settings.changed.connect(lambda k: k in ("theme", "accent") and self.changed.emit())
        fams = set(QFontDatabase.families())
        segoe = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), "")
        self._icon_set = "segoe" if segoe else "fluent"
        self._icon_font = segoe or _bundled_icon_font()
        # Windows' own type first; elsewhere the common UI fonts of Linux desktops and macOS
        self._display_font = next((f for f in ("Segoe UI Variable Display", "Segoe UI Variable", "Inter", "Segoe UI",
                                                "SF Pro Display", "Noto Sans", "Cantarell", "Ubuntu") if f in fams), "Sans Serif")
        self._text_font = next((f for f in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Inter",
                                             "SF Pro Text", "Noto Sans", "Cantarell", "Ubuntu") if f in fams), "Sans Serif")

    def _p(self):
        return PALETTES.get(self._s.value("theme"), PALETTES["dark"])

    def _tok(name, sig=changed):
        return Property(QColor, lambda self: QColor(self._p()[name]), notify=sig)

    bg = _tok("bg")
    bgAlt = _tok("bgAlt")
    surface = _tok("surface")
    surface2 = _tok("surface2")
    surface3 = _tok("surface3")
    text = _tok("text")
    textDim = _tok("textDim")
    textFaint = _tok("textFaint")
    stroke = _tok("stroke")
    strokeStrong = _tok("strokeStrong")
    glass = _tok("glass")
    glassStrong = _tok("glassStrong")
    shadow = _tok("shadow")
    inverse = _tok("inverse")
    inverseText = _tok("inverseText")
    scrim = _tok("scrim")
    del _tok

    @Property(bool, constant=True)
    def reduceMotion(self):
        """Windows "Mostrar animações" off (Acessibilidade › Efeitos visuais): skip big movements."""
        return _reduce_motion()

    @Property(bool, notify=changed)
    def dark(self):
        return self._s.value("theme") != "light"

    @Property(QColor, notify=changed)
    def accent(self):
        return QColor(self._s.value("accent"))

    @Property(QColor, notify=changed)
    def accentSoft(self):
        c = QColor(self._s.value("accent"))
        c.setAlphaF(0.16)
        return c

    @Property(QColor, notify=changed)
    def onAccent(self):
        c = QColor(self._s.value("accent"))
        lum = 0.299 * c.redF() + 0.587 * c.greenF() + 0.114 * c.blueF()
        return QColor("#141414" if lum > 0.72 else "#FFFFFF")

    @Property(list, constant=True)
    def accents(self):
        return [{"name": n, "color": c} for n, c in ACCENTS]

    @Property(str, constant=True)
    def iconFont(self):
        return self._icon_font

    @Property(str, constant=True)
    def iconSet(self):
        """Which glyph table qml/Icon.qml uses: "segoe" (Windows) or "fluent" (bundled font)."""
        return self._icon_set

    @Property(str, constant=True)
    def displayFont(self):
        return self._display_font

    @Property(str, constant=True)
    def textFont(self):
        return self._text_font
