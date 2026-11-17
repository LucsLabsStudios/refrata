"""What the screens can show: is Windows HDR ("Use HDR") on for a monitor, how bright it gets, and
the SDR content brightness the user chose in Settings › Display › HDR.

Windows only (elsewhere every screen reads as SDR). Two sources, both read-only and cheap:

  - DisplayConfig (QueryDisplayConfig + DisplayConfigGetDeviceInfo): per path, the advanced colour
    state and the SDR white level (the "SDR content brightness" slider, in units of 80/1000 nits)
  - DXGI (IDXGIOutput6::GetDesc1): per output, the colour space the desktop is composed in
    (G2084 = HDR on), the panel's peak and full-frame luminance from its EDID

Both are matched to Qt's screens by GDI device name (\\\\.\\DISPLAY1...), which is what
QScreen.name() returns on Windows. Never raises: anything unknown reads as an SDR screen.
"""
import ctypes
import sys
from ctypes import wintypes as wt

SDR_WHITE_DEFAULT = 80.0   # Windows' SDR white with the slider at 0 (the sRGB reference)


def _empty(name=""):
    return {"name": name, "hdr": False, "sdr_white": SDR_WHITE_DEFAULT, "max_nits": 0.0,
            "max_full_frame_nits": 0.0, "min_nits": 0.0, "bits": 8}


# ---------------------------------------------------------------- DisplayConfig
class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _PATH_SOURCE(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("statusFlags", ctypes.c_uint32)]


class _RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", ctypes.c_uint32), ("Denominator", ctypes.c_uint32)]


class _PATH_TARGET(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", ctypes.c_uint32), ("modeInfoIdx", ctypes.c_uint32),
                ("outputTechnology", ctypes.c_uint32), ("rotation", ctypes.c_uint32),
                ("scaling", ctypes.c_uint32), ("refreshRate", _RATIONAL),
                ("scanLineOrdering", ctypes.c_uint32), ("targetAvailable", wt.BOOL),
                ("statusFlags", ctypes.c_uint32)]


class _PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", _PATH_SOURCE), ("targetInfo", _PATH_TARGET), ("flags", ctypes.c_uint32)]


class _MODE_INFO(ctypes.Structure):
    _fields_ = [("infoType", ctypes.c_uint32), ("id", ctypes.c_uint32), ("adapterId", _LUID),
                ("data", ctypes.c_byte * 48)]


class _HEADER(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_uint32), ("adapterId", _LUID),
                ("id", ctypes.c_uint32)]


class _SOURCE_NAME(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("viewGdiDeviceName", ctypes.c_wchar * 32)]


class _ADVANCED_COLOR(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("value", ctypes.c_uint32), ("colorEncoding", ctypes.c_uint32),
                ("bitsPerColorChannel", ctypes.c_uint32)]


class _SDR_WHITE(ctypes.Structure):
    _fields_ = [("header", _HEADER), ("SDRWhiteLevel", ctypes.c_ulong)]


_GET_SOURCE_NAME = 1
_GET_ADVANCED_COLOR_INFO = 9
_GET_SDR_WHITE_LEVEL = 11
_QDC_ONLY_ACTIVE_PATHS = 2


def _displayconfig():
    """{gdi name: {hdr, sdr_white, bits}} from DisplayConfig."""
    out = {}
    u = ctypes.windll.user32
    n_path, n_mode = ctypes.c_uint32(), ctypes.c_uint32()
    if u.GetDisplayConfigBufferSizes(_QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_path), ctypes.byref(n_mode)):
        return out
    paths = (_PATH_INFO * n_path.value)()
    modes = (_MODE_INFO * n_mode.value)()
    if u.QueryDisplayConfig(_QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_path), paths,
                            ctypes.byref(n_mode), modes, None):
        return out
    for p in paths[:n_path.value]:
        src = _SOURCE_NAME()
        src.header.type, src.header.size = _GET_SOURCE_NAME, ctypes.sizeof(src)
        src.header.adapterId, src.header.id = p.sourceInfo.adapterId, p.sourceInfo.id
        if u.DisplayConfigGetDeviceInfo(ctypes.byref(src)):
            continue
        info = {}
        ac = _ADVANCED_COLOR()
        ac.header.type, ac.header.size = _GET_ADVANCED_COLOR_INFO, ctypes.sizeof(ac)
        ac.header.adapterId, ac.header.id = p.targetInfo.adapterId, p.targetInfo.id
        if not u.DisplayConfigGetDeviceInfo(ctypes.byref(ac)):
            # bit 0 advancedColorSupported, bit 1 advancedColorEnabled, bit 3 forceDisabled
            info["hdr"] = bool(ac.value & 0x2) and not ac.value & 0x8
            info["bits"] = int(ac.bitsPerColorChannel) or 8
        sw = _SDR_WHITE()
        sw.header.type, sw.header.size = _GET_SDR_WHITE_LEVEL, ctypes.sizeof(sw)
        sw.header.adapterId, sw.header.id = p.targetInfo.adapterId, p.targetInfo.id
        if not u.DisplayConfigGetDeviceInfo(ctypes.byref(sw)) and sw.SDRWhiteLevel:
            info["sdr_white"] = sw.SDRWhiteLevel * 80.0 / 1000.0
        out[src.viewGdiDeviceName] = info
    return out


# ---------------------------------------------------------------- DXGI
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _guid(s):
    import uuid
    b = uuid.UUID(s).bytes_le
    return _GUID.from_buffer_copy(b)


class _OUTPUT_DESC1(ctypes.Structure):
    _fields_ = [("DeviceName", ctypes.c_wchar * 32), ("DesktopCoordinates", wt.RECT),
                ("AttachedToDesktop", wt.BOOL), ("Rotation", ctypes.c_uint32),
                ("Monitor", wt.HMONITOR), ("BitsPerColor", ctypes.c_uint32),
                ("ColorSpace", ctypes.c_uint32), ("RedPrimary", ctypes.c_float * 2),
                ("GreenPrimary", ctypes.c_float * 2), ("BluePrimary", ctypes.c_float * 2),
                ("WhitePoint", ctypes.c_float * 2), ("MinLuminance", ctypes.c_float),
                ("MaxLuminance", ctypes.c_float), ("MaxFullFrameLuminance", ctypes.c_float)]


_G2084 = 12   # DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020


def _com(obj, index, restype, *argtypes):
    vt = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vt[index])


def _release(obj):
    if obj:
        _com(obj, 2, ctypes.c_ulong)(obj)


def _dxgi():
    """{gdi name: {hdr, max_nits, max_full_frame_nits, min_nits, bits}} from DXGI outputs."""
    out = {}
    dxgi = ctypes.windll.dxgi
    factory = ctypes.c_void_p()
    if dxgi.CreateDXGIFactory1(ctypes.byref(_guid("770aae78-f26f-4dba-a829-253c83d1b387")),
                               ctypes.byref(factory)):
        return out
    iid6 = _guid("068346e8-aaec-4b84-add7-137f513f77a1")
    try:
        a = 0
        while a < 16:
            adapter = ctypes.c_void_p()
            # IDXGIFactory1::EnumAdapters1 (vtable 12)
            if _com(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                    factory, a, ctypes.byref(adapter)):
                break
            try:
                o = 0
                while o < 16:
                    output = ctypes.c_void_p()
                    # IDXGIAdapter::EnumOutputs (vtable 7)
                    if _com(adapter, 7, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                            adapter, o, ctypes.byref(output)):
                        break
                    try:
                        o6 = ctypes.c_void_p()
                        if not _com(output, 0, ctypes.c_long, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
                                output, ctypes.byref(iid6), ctypes.byref(o6)):
                            try:
                                d = _OUTPUT_DESC1()
                                # IDXGIOutput6::GetDesc1 (vtable 27)
                                if not _com(o6, 27, ctypes.c_long, ctypes.POINTER(_OUTPUT_DESC1))(o6, ctypes.byref(d)):
                                    out[d.DeviceName] = {
                                        "hdr": d.ColorSpace == _G2084,
                                        "max_nits": float(d.MaxLuminance),
                                        "max_full_frame_nits": float(d.MaxFullFrameLuminance),
                                        "min_nits": float(d.MinLuminance),
                                        "bits": int(d.BitsPerColor) or 8}
                            finally:
                                _release(o6)
                    finally:
                        _release(output)
                    o += 1
            finally:
                _release(adapter)
            a += 1
    finally:
        _release(factory)
    return out


_cache = [0.0, {}]
CACHE_S = 1.0


class _ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                ("AdapterLuid", _LUID), ("Flags", ctypes.c_uint)]


def gpus() -> list[dict]:
    """The graphics adapters (not the software one): name, vendor id, dedicated memory in MB."""
    if sys.platform != "win32":
        return []
    out = []
    try:
        dxgi = ctypes.windll.dxgi
        factory = ctypes.c_void_p()
        if dxgi.CreateDXGIFactory1(ctypes.byref(_guid("770aae78-f26f-4dba-a829-253c83d1b387")), ctypes.byref(factory)):
            return []
        try:
            for a in range(16):
                adapter = ctypes.c_void_p()
                if _com(factory, 12, ctypes.c_long, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                        factory, a, ctypes.byref(adapter)):
                    break
                try:
                    d = _ADAPTER_DESC1()
                    # IDXGIAdapter1::GetDesc1 (vtable 10)
                    if not _com(adapter, 10, ctypes.c_long, ctypes.POINTER(_ADAPTER_DESC1))(adapter, ctypes.byref(d)):
                        if not d.Flags & 2:   # DXGI_ADAPTER_FLAG_SOFTWARE
                            out.append({"name": d.Description, "vendor": d.VendorId,
                                        "vram_mb": d.DedicatedVideoMemory // (1 << 20)})
                finally:
                    _release(adapter)
        finally:
            _release(factory)
    except Exception:
        pass
    return out


def screens():
    """{gdi name: info} for every active display; {} when it cannot be read. Read again at most
    once a second (startup asks twice in a row)."""
    if sys.platform != "win32":
        return {}
    import time
    now = time.monotonic()
    if now - _cache[0] < CACHE_S:
        return {k: dict(v) for k, v in _cache[1].items()}
    res = _read_all()
    _cache[0], _cache[1] = now, res
    return {k: dict(v) for k, v in res.items()}


def _read_all():
    res = {}
    for part in (_dxgi, _displayconfig):
        try:
            for name, info in part().items():
                res.setdefault(name, _empty(name)).update(info)
        except Exception:
            pass
    for info in res.values():
        # an EDID without luminance data: assume a modest HDR panel rather than none
        if info["hdr"] and info["max_nits"] < 100:
            info["max_nits"] = 400.0
    return res


def screen(name):
    """Info for one screen by its GDI device name (\\\\.\\DISPLAY1); SDR defaults when unknown.
    (Qt's QScreen.name() is the monitor's model name on Windows: use screen_of_window.)"""
    return screens().get(name) or _empty(name)


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


def device_of_window(hwnd: int) -> str:
    """The GDI device name (\\\\.\\DISPLAY1) of the monitor a window is on; "" if unknown."""
    if sys.platform != "win32" or not hwnd:
        return ""
    try:
        u = ctypes.windll.user32
        u.MonitorFromWindow.restype = wt.HMONITOR
        mon = u.MonitorFromWindow(wt.HWND(hwnd), 2)        # MONITOR_DEFAULTTONEAREST
        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(mi)
        if mon and u.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return mi.szDevice
    except Exception:
        pass
    return ""


def screen_of_window(hwnd: int):
    """Info for the screen a window is on."""
    return screen(device_of_window(hwnd))


if __name__ == "__main__":
    import json
    print(json.dumps(screens(), indent=2))
