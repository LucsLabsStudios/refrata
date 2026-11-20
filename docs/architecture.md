# Architecture

Refrata is a Python application on Qt for Python (PySide6) and libVLC 3. Python owns playback,
the library and the operating system integration; the whole interface is Qt Quick (QML), drawn
on the GPU.

## Layout

| Path | Role |
|---|---|
| `src/refrata/__main__.py` | Startup: fast single-instance hand-off, libVLC discovery, Qt setup, `--selftest`, `--screenshot` |
| `src/refrata/engine.py` | libVLC playback: queue, tracks, equalizer, picture adjustments, chapters, Chromecast; video frames into `QVideoSink` |
| `src/refrata/library.py` | Folder scanning, filterable models, thumbnails on a thread with its own libVLC instance |
| `src/refrata/scrub_thumbs.py` | Timeline hover preview: a second, paused decoder and a progressive storyboard |
| `src/refrata/settings.py` | Preferences, history and resume positions in `%LOCALAPPDATA%\Refrata` |
| `src/refrata/single.py`, `fastlaunch.py` | Single instance over a per-user `QLocalServer`; the fast path hands files over before Qt loads |
| `src/refrata/updater.py`, `ed25519.py` | Update checks on GitHub Releases, SHA-256 and Ed25519 verification, silent install |
| `src/refrata/quicklook.py` | Space-bar preview in Explorer (keyboard hook plus Explorer automation) |
| `src/refrata/mediakeys.py` | Windows media keys and overlay (SystemMediaTransportControls) |
| `src/refrata/mpris.py`, `mpris_wire.py` | Linux media keys and desktop widgets (MPRIS over D-Bus) |
| `src/refrata/websites.py`, `ytdlp.py` | Site links resolved with yt-dlp, which keeps itself current |
| `src/refrata/subsearch.py` | OpenSubtitles.com search |
| `src/refrata/i18n.py` | Runtime translation and plural rules for 14 languages |
| `src/refrata/ui/` | Every screen and control, in QML |
| `src/refrata/resources/` | Window and tray icons, icon font, brand images |
| `src/refrata/locales/` | One JSON file per language |
| `packaging/windows/` | Inno Setup script and art; MSIX manifest assets |
| `tools/` | Build, release, signing, localization, coverage and brand tooling |
| `tests/` | Synthetic fixtures and every test suite |

## Video path

libVLC decodes into a CPU buffer through its video callbacks (`vmem`). The engine hands each
picture to Qt as an NV12 `QVideoFrame`, and Qt converts the color on the GPU. Frames are dropped
when the interface is busy rather than queued.

Taking the pictures this way, instead of letting VLC draw into a native window, is what lets
the interface draw anything over the video, including the live blur behind the glass panels,
without the windowing problems of the classic VLC approach.

libVLC 3 turns hardware decoding off for applications that use video callbacks. Refrata sets
`avcodec-hw` per media to keep it on, which cuts CPU use on 4K by about 70%. GPU-decoded pictures
carry no subtitles on this path, so an item that shows subtitles reopens in software decoding at
the same position.

## HDR output

On a Windows HDR screen the window's swapchain is scRGB (16-bit float) and HDR video is drawn at
its real brightness; elsewhere it is 8-bit and HDR is tone mapped. `hdroutput.py` keeps the two in
step with the screen while the app runs:

- A hidden window on its own thread hears `WM_DISPLAYCHANGE`, then reads the screen's state
  (`hdrdisplay.py`) a few times while Windows settles, plus a light poll every 2 s. Nothing of this
  runs on the interface or render thread.
- When the screen and the swapchain disagree, the window's native surface is destroyed and shown
  again in the same place, size and full-screen state. Qt keeps its device, so the picture is back
  in under 100 ms and playback does not stop.
- Qt 6.8 asks DXGI whether the output is in HDR through an adapter made at startup, which keeps
  the state of that moment. `native/src/dxgihook.cpp` patches `IDXGIOutput6::GetDesc1` to answer
  with the current state for the screens Refrata watches, so HDR turned on after launch works too.
- If a driver ever refuses anyway, the window shows **Reopen in HDR**: `reopen.py` starts a new
  instance with the same queue, position, page and window, and the old one closes.

`tests/winhdr.py` switches Windows HDR for real; `tests/robust.py` has simulated and real switch
scenarios (the real ones need `REFRATA_REAL_HDR=1`).

## Startup

A double-click on a file while Refrata runs must answer at once. `fastlaunch.py` runs before Qt
and libVLC are imported and hands the file to the running instance in about 0.2 s; only when no
instance answers does the full startup continue. Only then, on a real cold start, the packaged app
shows its splash (`launchsplash.py`, a native layered window on its own thread), so handing a file
to the running Refrata never flashes it; it also starts loading the file while the window is still
being built.

## Data

Settings, history and caches live in `%LOCALAPPDATA%\Refrata` on Windows and `~/Refrata` on
Linux (`src/refrata/settings.py`). Corrupt or unreadable files are ignored and rebuilt rather than blocking startup. Tests
point `REFRATA_DATA` at a temporary folder and never touch the user's own data.
