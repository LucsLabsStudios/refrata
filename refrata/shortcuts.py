"""Keyboard shortcuts the user can change in Ajustes › Atalhos de teclado.

Each action has one key. QML binds its Shortcut items to `keys.map.<action>`, so a change applies
at once. Only the keys that differ from the defaults are stored (settings "shortcuts"), so a
default changed in a later version still reaches everyone who never touched that action.
Esc (leave full screen / close) and F11 stay fixed, as everywhere on Windows.
"""
from PySide6.QtCore import Property, QObject, Qt, Signal, Slot
from PySide6.QtGui import QKeySequence

from .i18n import tr

# action id -> default key, in the order Ajustes lists them
DEFAULTS = {
    "playPause": "Space",
    "fullscreen": "F",
    "back10": "Left",
    "fwd10": "Right",
    "back3": "Shift+Left",
    "fwd3": "Shift+Right",
    "back60": "Ctrl+Left",
    "fwd60": "Ctrl+Right",
    "volUp": "Up",
    "volDown": "Down",
    "mute": "M",
    "next": "N",
    "prev": "P",
    "nextChapter": "Shift+N",
    "prevChapter": "Shift+P",
    "faster": "]",
    "slower": "[",
    "normalSpeed": "=",
    "snapshot": "S",
    "frame": "E",
    "loop": "L",
    "onTop": "T",
    "info": "I",
    "rotate": "R",
    "pip": "Ctrl+P",
    "open": "Ctrl+O",
    "stream": "Ctrl+L",
    "settings": "Ctrl+,",
}
RESERVED = {"Esc", "F11"}

_MODIFIER_KEYS = {Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
                  Qt.Key.Key_CapsLock, Qt.Key.Key_NumLock, Qt.Key.Key_ScrollLock, Qt.Key.Key_unknown}
_ARROWS = {"Left": "←", "Right": "→", "Up": "↑", "Down": "↓"}


def normalize(seq: str) -> str:
    """One spelling per key ("ctrl+o" and "Ctrl+O" are the same shortcut)."""
    return QKeySequence(seq).toString(QKeySequence.SequenceFormat.PortableText) if seq else ""


class Shortcuts(QObject):
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._map = {}
        self._load()

    def _load(self):
        stored = self._settings.value("shortcuts") or {}
        m = dict(DEFAULTS)
        for k, v in stored.items():
            if k in m and isinstance(v, str):
                m[k] = normalize(v)
        # a hand-edited file could give two actions one key: the later one in the list loses it
        seen = set()
        for k in DEFAULTS:
            if m[k] and (m[k] in seen or m[k] in RESERVED):
                m[k] = ""
            seen.add(m[k])
        self._map = m

    def _save(self):
        self._settings.setValue("shortcuts", {k: v for k, v in self._map.items() if v != DEFAULTS[k]})
        self.changed.emit()

    # ------------------------------------------------------------------ QML API
    @Property("QVariantMap", notify=changed)
    def map(self):
        return dict(self._map)

    @Property("QVariantList", constant=True)
    def actions(self):
        return list(DEFAULTS)

    @Property(bool, notify=changed)
    def customized(self):
        return any(v != DEFAULTS[k] for k, v in self._map.items())

    @Slot(str, result=str)
    def keyOf(self, action):
        return self._map.get(action, "")

    @Slot(str, str, result=str)
    def assign(self, action, seq):
        """Give `action` the key `seq`. Returns the action that had that key (it is left without
        one), "" if none, or "reserved" when the key can't be used."""
        seq = normalize(seq)
        if action not in DEFAULTS or not seq:
            return ""
        if seq in RESERVED:
            return "reserved"
        took = ""
        for k, v in self._map.items():
            if v == seq and k != action:
                self._map[k] = ""
                took = k
        self._map[action] = seq
        self._save()
        return took

    @Slot(str)
    def reset(self, action):
        if action in DEFAULTS:
            d = DEFAULTS[action]
            for k, v in self._map.items():
                if v == d and k != action:
                    self._map[k] = ""
            self._map[action] = d
            self._save()

    @Slot()
    def resetAll(self):
        self._map = dict(DEFAULTS)
        self._save()

    @Slot(int, int, result=str)
    def fromKey(self, key, modifiers):
        """The shortcut text for a key press (Keys.onPressed's event.key / event.modifiers);
        "" for a lone modifier, which is still waiting for its key."""
        try:
            k = Qt.Key(key)
        except ValueError:
            return ""
        if k in _MODIFIER_KEYS:
            return ""
        mods = Qt.KeyboardModifier(modifiers) & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier
                                                 | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        # Shift+[ arrives as "{" on most layouts: keep the key the user sees on the cap
        seq = QKeySequence(int(k) | int(mods.value if hasattr(mods, "value") else mods))
        return seq.toString(QKeySequence.SequenceFormat.PortableText)

    @Slot(str, result=str)
    def label(self, seq):
        """How a key is shown: arrows as arrows, Space in the app's language."""
        if not seq:
            return "—"
        parts = seq.split("+") if seq != "+" else ["+"]
        if seq.endswith("++"):
            parts = seq[:-2].split("+") + ["+"]
        out = []
        for p in parts:
            if p == "Space":
                out.append(tr("Espaço"))
            else:
                out.append(_ARROWS.get(p, p))
        return " + ".join(out)
