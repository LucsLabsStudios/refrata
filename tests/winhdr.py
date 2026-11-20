"""Switch Windows HDR ("Use HDR") on a display, the way Settings › Display does
(DisplayConfigSetDeviceInfo with DISPLAYCONFIG_DEVICE_INFO_SET_ADVANCED_COLOR_STATE).

For tests that check the app following a real HDR switch (robust.py, REFRATA_REAL_HDR=1). The
screen goes black for about a second each time. Command line: python tests/winhdr.py [on|off]
"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from refrata import hdrdisplay as hd   # noqa: E402

_SET_ADVANCED_COLOR_STATE = 10


class _SET_STATE(ctypes.Structure):
    _fields_ = [("header", hd._HEADER), ("value", ctypes.c_uint32)]


def displays():
    """[(gdi name, adapterId, target id, supported, enabled)] for the active displays."""
    u = ctypes.windll.user32
    n_path, n_mode = ctypes.c_uint32(), ctypes.c_uint32()
    if u.GetDisplayConfigBufferSizes(hd._QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_path), ctypes.byref(n_mode)):
        return []
    paths = (hd._PATH_INFO * n_path.value)()
    modes = (hd._MODE_INFO * n_mode.value)()
    if u.QueryDisplayConfig(hd._QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_path), paths,
                            ctypes.byref(n_mode), modes, None):
        return []
    out = []
    for p in paths[:n_path.value]:
        src = hd._SOURCE_NAME()
        src.header.type, src.header.size = hd._GET_SOURCE_NAME, ctypes.sizeof(src)
        src.header.adapterId, src.header.id = p.sourceInfo.adapterId, p.sourceInfo.id
        if u.DisplayConfigGetDeviceInfo(ctypes.byref(src)):
            continue
        ac = hd._ADVANCED_COLOR()
        ac.header.type, ac.header.size = hd._GET_ADVANCED_COLOR_INFO, ctypes.sizeof(ac)
        ac.header.adapterId, ac.header.id = p.targetInfo.adapterId, p.targetInfo.id
        if u.DisplayConfigGetDeviceInfo(ctypes.byref(ac)):
            continue
        out.append((src.viewGdiDeviceName, p.targetInfo.adapterId, p.targetInfo.id,
                    bool(ac.value & 1), bool(ac.value & 2) and not ac.value & 8))
    return out


def set_hdr(on: bool, name: str = "", wait: float = 8.0) -> bool:
    """Switch HDR on the display `name` (or the first that supports it); True once DXGI reports
    the new state."""
    u = ctypes.windll.user32
    for dev, adapter, target, supported, enabled in displays():
        if not supported or (name and dev != name):
            continue
        if enabled != on:
            s = _SET_STATE()
            s.header.type, s.header.size = _SET_ADVANCED_COLOR_STATE, ctypes.sizeof(s)
            s.header.adapterId, s.header.id = adapter, target
            s.value = 1 if on else 0
            if u.DisplayConfigSetDeviceInfo(ctypes.byref(s)):
                return False
        end = time.time() + wait
        while time.time() < end:
            if bool(hd.screens(fresh=True).get(dev, {}).get("hdr")) == on:
                return True
            time.sleep(0.1)
        return False
    return False


def hdr_display():
    """(gdi name, enabled) of the first display that supports HDR, or None."""
    for dev, _a, _t, supported, enabled in displays():
        if supported:
            return dev, enabled
    return None


if __name__ == "__main__":
    print(hdr_display())
    if len(sys.argv) > 1:
        print(set_hdr(sys.argv[1] == "on"))
        print(hdr_display())
