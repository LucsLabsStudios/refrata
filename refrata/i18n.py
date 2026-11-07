"""Localisation.

The source language is Brazilian Portuguese: QML marks text with qsTr("…") and Python with tr("…").
Each other language lives in i18n/<code>.json, mapping the Portuguese source to a translation
written for that language (not word for word). A plural entry maps to {"one": …, "few": …,
"many": …, "other": …} with the forms that language uses; "%n" is the count.

A small QTranslator subclass serves those files, so switching language is live: install the new
translator, then QQmlEngine.retranslate() re-evaluates every binding that called qsTr.
"""
import json
import os

from PySide6.QtCore import Property, QCoreApplication, QLocale, QObject, QTranslator, Signal, Slot

from .vlcpath import app_root

I18N_DIR = app_root() / "i18n"

# code, native name, Qt locale (dates and numbers)
LANGUAGES = [
    ("pt", "Português (Brasil)", "pt_BR"),
    ("en", "English", "en_US"),
    ("es", "Español", "es_ES"),
    ("fr", "Français", "fr_FR"),
    ("de", "Deutsch", "de_DE"),
    ("it", "Italiano", "it_IT"),
    ("ru", "Русский", "ru_RU"),
    ("pl", "Polski", "pl_PL"),
    ("tr", "Türkçe", "tr_TR"),
    ("id", "Bahasa Indonesia", "id_ID"),
    ("hi", "हिन्दी", "hi_IN"),
    ("zh", "简体中文", "zh_CN"),
    ("ja", "日本語", "ja_JP"),
    ("ko", "한국어", "ko_KR"),
]
CODES = [c for c, _n, _l in LANGUAGES]
SOURCE = "pt"


def plural_form(code: str, n: int) -> str:
    """CLDR plural category for the languages we ship."""
    n = abs(int(n))
    if code in ("zh", "ja", "ko", "id"):
        return "other"
    if code in ("pt", "fr", "hi"):
        return "one" if n in (0, 1) else "other"
    if code == "ru":
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    if code == "pl":
        if n == 1:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    return "one" if n == 1 else "other"   # en, es, de, it, tr


def load(code: str) -> dict:
    try:
        with open(I18N_DIR / f"{code}.json", encoding="utf8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


class JsonTranslator(QTranslator):
    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.code = code
        self.table = load(code)

    def isEmpty(self):
        return not self.table

    def translate(self, context, source, disambiguation=None, n=-1):
        v = self.table.get(source)
        if isinstance(v, dict):
            v = v.get(plural_form(self.code, n if n is not None and n >= 0 else 1)) or v.get("other")
        return v or None   # None: Qt keeps the source text


def tr(text: str, n: int = -1) -> str:
    """Python-side qsTr (the context is ignored by our translator)."""
    return QCoreApplication.translate("Refrata", text, None, n)


def N_(text: str) -> str:
    """Marks a string for extraction; QML translates it where it is shown (qsTr(name))."""
    return text


def system_language() -> str:
    for tag in QLocale.system().uiLanguages():
        code = tag.replace("_", "-").split("-")[0].lower()
        if code in CODES:
            return code
    return "en"


class I18n(QObject):
    """QML: `i18n.language` ("auto" or a code), `i18n.languages`, `i18n.locale`."""
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._qml = None
        self._translator = None
        self._apply()

    def attach(self, qml_engine):
        self._qml = qml_engine

    def _choice(self) -> str:
        v = str(self._settings.value("language", "auto") or "auto")
        return v if v in CODES else "auto"

    def _effective(self) -> str:
        c = self._choice()
        if c != "auto":
            return c
        forced = os.environ.get("REFRATA_LANG")   # tests / screenshots: stand-in for the system language
        return forced if forced in CODES else system_language()

    def _apply(self):
        app = QCoreApplication.instance()
        if self._translator is not None:
            app.removeTranslator(self._translator)
            self._translator = None
        code = self._effective()
        # Portuguese is the source, but its file still carries the plural forms
        t = JsonTranslator(code, self)
        if not t.isEmpty():
            app.installTranslator(t)
            self._translator = t
        QLocale.setDefault(QLocale(self.locale))
        self._code = code

    @Property(str, notify=changed)
    def language(self):
        return self._choice()

    @language.setter
    def language(self, code):
        code = code if code in CODES else "auto"
        if code == self._choice():
            return
        self._settings.setValue("language", code)
        self._apply()
        if self._qml is not None:
            self._qml.retranslate()
        self.changed.emit()

    @Property(str, notify=changed)
    def current(self):
        return self._code

    @Property(str, notify=changed)
    def locale(self):
        return dict((c, l) for c, _n, l in LANGUAGES).get(self._effective(), "en_US")

    @Property(str, constant=True)
    def system(self):
        """The language "auto" resolves to."""
        forced = os.environ.get("REFRATA_LANG")
        return forced if forced in CODES else system_language()

    @Property(list, constant=True)
    def languages(self):
        return [{"code": c, "name": n} for c, n, _l in LANGUAGES]

    @Slot(str, result=str)
    def nameOf(self, code):
        return dict((c, n) for c, n, _l in LANGUAGES).get(code, code)
