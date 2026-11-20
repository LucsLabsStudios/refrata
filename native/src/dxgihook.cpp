// (Windows) The screen's HDR state as it is now, for Qt's HDR checks.
//
// Qt 6.8 decides whether a window may have an HDR (scRGB) swapchain by asking DXGI for the
// window's output colour space (IDXGIOutput6::GetDesc1) through the adapter of its own device.
// DXGI answers through an adapter with what the screen was when that adapter's factory was made:
// switch Windows HDR on after Refrata started and Qt keeps hearing "SDR", so it never makes the
// HDR swapchain (refrata/hdroutput.py). Chrome, with its own DXGI code, just asks a new factory.
//
// Here: GetDesc1 of every DXGI output in this process is wrapped (one vtable slot: all outputs
// share the class) and, for a display Refrata has fresh news of (rv_dxgi_override, from a new
// factory and DisplayConfig, hdrdisplay.py), the colour space reported is the current one. Only
// the colour space; everything else is DXGI's own answer.
#include <windows.h>
#include <dxgi1_6.h>
#include <mutex>
#include <string>
#include <unordered_map>

namespace {

using GetDesc1Fn = HRESULT(STDMETHODCALLTYPE *)(IDXGIOutput6 *, DXGI_OUTPUT_DESC1 *);
GetDesc1Fn g_orig = nullptr;
std::mutex g_lock;
std::unordered_map<std::wstring, int> g_hdr;   // GDI name -> 1 HDR, 0 SDR
long g_hits = 0;                               // answers changed (tests)

HRESULT STDMETHODCALLTYPE hooked(IDXGIOutput6 *self, DXGI_OUTPUT_DESC1 *d) {
    const HRESULT hr = g_orig(self, d);
    if (FAILED(hr) || !d)
        return hr;
    std::lock_guard<std::mutex> l(g_lock);
    auto it = g_hdr.find(d->DeviceName);
    if (it == g_hdr.end())
        return hr;
    const bool hdr_now = it->second == 1;
    const bool hdr_said = d->ColorSpace == DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020;
    if (hdr_now != hdr_said) {
        d->ColorSpace = hdr_now ? DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020 : DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709;
        if (hdr_now && d->BitsPerColor < 10)
            d->BitsPerColor = 10;
        ++g_hits;
    }
    return hr;
}

} // namespace

extern "C" {

// 1 once GetDesc1 is wrapped (or already was), 0 when there is no DXGI output to find it through
int rv_dxgi_hook_install() {
    static std::once_flag once;
    static int ok = 0;
    std::call_once(once, [] {
        IDXGIFactory1 *f = nullptr;
        if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void **>(&f))))
            return;
        IDXGIAdapter1 *a = nullptr;
        IDXGIOutput *o = nullptr;
        for (UINT i = 0; !o && f->EnumAdapters1(i, &a) != DXGI_ERROR_NOT_FOUND; ++i) {
            a->EnumOutputs(0, &o);
            a->Release();
        }
        f->Release();
        if (!o)
            return;
        IDXGIOutput6 *o6 = nullptr;
        if (SUCCEEDED(o->QueryInterface(__uuidof(IDXGIOutput6), reinterpret_cast<void **>(&o6)))) {
            void **vtbl = *reinterpret_cast<void ***>(o6);
            void **slot = &vtbl[27];                     // IDXGIOutput6::GetDesc1
            DWORD prot = 0;
            if (*slot != reinterpret_cast<void *>(&hooked)
                && VirtualProtect(slot, sizeof(void *), PAGE_READWRITE, &prot)) {
                g_orig = reinterpret_cast<GetDesc1Fn>(*slot);
                InterlockedExchangePointer(slot, reinterpret_cast<void *>(&hooked));
                VirtualProtect(slot, sizeof(void *), prot, &prot);
                FlushInstructionCache(GetCurrentProcess(), slot, sizeof(void *));
                ok = 1;
            }
            o6->Release();
        }
        o->Release();
    });
    return ok;
}

// what the display `name` (\\.\DISPLAY1) is now: 1 HDR, 0 SDR, -1 forget it (DXGI answers alone)
void rv_dxgi_override(const wchar_t *name, int hdr) {
    std::lock_guard<std::mutex> l(g_lock);
    if (hdr < 0)
        g_hdr.erase(name);
    else
        g_hdr[name] = hdr ? 1 : 0;
}

long rv_dxgi_hits() {
    std::lock_guard<std::mutex> l(g_lock);
    return g_hits;
}

} // extern "C"
