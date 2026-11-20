# Development guide

How to run Refrata from source, test it, package it and publish a release.

- [Set up](#set-up)
- [Run](#run)
- [Test](#test)
- [Package](#package)
- [Release](#release)
- [Update signing key](#update-signing-key)
- [Bundled components](#bundled-components)
- [Code signing](#code-signing)
- [Translations](#translations)
- [Logs](#logs)

## Set up

Requirements: Windows 10/11 x64 (or Linux for the Linux build), Python 3.12, and
[Inno Setup 6](https://jrsoftware.org/isinfo.php) for the Windows installer
(`winget install JRSoftware.InnoSetup`).

```powershell
pip install -r requirements.txt -r requirements-dev.txt
```

`requirements.txt` pins the exact versions every release is tested with; `tools/release.py`
updates it. To get a `refrata` command instead of running the file, install the package in
editable mode with `pip install -e .`.

libVLC is found in this order (`src/refrata/vlcpath.py`):

1. `PYTHON_VLC_LIB_PATH`, when set
2. the copy bundled with the packaged app (`_internal/vlc`)
3. `vendor/vlc` at the repository root, which `tools/build.py` fills
4. a VLC 3 install in Program Files

## Run

```powershell
python src/refrata/__main__.py                      # the app
python src/refrata/__main__.py "C:\film.mkv"        # open files, folders, playlists or URLs
python src/refrata/__main__.py --new-window         # skip the single-instance hand-off
python src/refrata/__main__.py --screenshot out.png --page player --panel eq "C:\video.mp4"
python src/refrata/__main__.py --screenshot out.png --page settings --panel about
```

## Test

| Command | What it covers | Time |
|---|---|---|
| `python tests/unit.py` | Playlists, updater, signatures, single instance, settings, localization rules | seconds |
| `python tests/e2e.py` | The real app (QML + libVLC) with isolated data and generated media: every control clicked with the mouse, keyboard, panels, full screen, drag and drop, native Windows dialogs, single instance, shutdown | ~5 min |
| `python tests/e2e.py -k t35,t40` | Only the tests whose name contains one of the filters | |
| `python tests/e2e_parallel.py --exe dist\Refrata\Refrata.exe` | The e2e suite split across several app processes, against the packaged app | ~2 min |
| `python tests/robust.py` | 108 scenarios, each in its own app process that exits through the real quit path | ~10 min |
| `python tests/launch.py --exe dist\Refrata\Refrata.exe` | Cold start, hand-off, idle CPU and memory, 1080p30 and 4K60 playback budgets | ~1 min |
| `python tests/test_installer.py` | Installs, tests the installed copy, opens a file through the registry, uninstalls | |
| `python tests/test_msix.py` | The same for the MSIX package (see [microsoft-store.md](microsoft-store.md)) | |
| `python tests/test_ed25519.py` | The Ed25519 implementation against the RFC 8032 vectors | seconds |
| `python tools/ui_coverage.py` | Every interface control is used by at least one test | seconds |

Any Qt or QML warning fails a test. The robustness suite also fails a scenario that leaves a
`crash.log`, a non-zero exit code or an invalid `settings.json`. It covers:

- **Quitting in every state:** loading, playing, paused, ended, music mode, full screen, quick
  player, during a library scan, mid-seek, while streaming.
- **Stress:** non-stop next/previous, opening files while loading, a 1,000-item queue, a
  2,620-file library, files deleted or renamed in use, memory across 200 track changes.
- **Corrupt data:** unreadable, empty or mistyped `settings.json`, an invalid library cache,
  read-only data files.
- **Formats** generated with libVLC (`tests/formats.py`): MP4, MOV, MKV, OGV, AVI, TS, MPEG-2,
  HEVC, 4K, portrait, MP3, OGG, Opus, M4A, FLAC, 5.1, WAV; SRT, VTT, ASS and Latin-1 subtitles;
  empty, truncated and mislabeled files; accented, Japanese and emoji names; paths over 260
  characters.
- **Layout:** all 14 languages on every page and panel with no clipped text, also at 150% scale
  with the window at its minimum size.
- **Monkey:** seeded random clicks and keys for two minutes.
- **HDR and picture:** Windows HDR switched while a video plays (simulated), windowed and full
  screen, with the window's place and size kept; 20 picture option switches while playing; reopen
  in place.

Some scenarios need real hardware and only run when asked:

| Variable | Runs |
|---|---|
| `REFRATA_REAL_HDR=1` | Switches Windows HDR on the real screen (`tests/winhdr.py`), checks the swapchain follows, and turns it back as it was. Needs an HDR screen. |
| `REFRATA_BENCH_DIR=<folder>` | Plays every video in the folder and reports frame pacing; `REFRATA_BENCH_SECONDS` sets the time per video, `REFRATA_BENCH_HIDDEN=1` measures with the window hidden. |

`python tests/winhdr.py on` and `off` switch Windows HDR by hand.

The packaged app runs the same suite from inside: `Refrata.exe --selftest --report r.json`.

The keyboard tests need the window in front. If another app takes the focus while they run, the
suite brings the window back before typing and reports it at the end.

On Linux the suite runs under a virtual display and a private D-Bus session, with a PulseAudio
null sink as the sound device (`.github/workflows/linux.yml`):

```bash
pulseaudio -D --exit-idle-time=-1 && pactl load-module module-null-sink
dbus-run-session -- xvfb-run -a -s "-screen 0 1920x1080x24" python src/refrata/__main__.py --selftest
```

## Package

```powershell
python tools/build.py                              # vendor/vlc + PyInstaller + portable zip + installer + MSIX
python tools/build.py --no-installer --no-msix     # only dist/Refrata and the zip
python tools/msix.py                               # only the MSIX packages
python tools/build.py --vlc-zip vlc-3.0.24-win64.zip   # use a VLC zip you already have
python tools/build.py --system-vlc                 # use the VLC installed on this PC
```

Output goes to `dist/`. Inside the packaged app the interface, resources and translations sit in
`_internal/ui`, `_internal/resources` and `_internal/locales`, next to `_internal/vlc`.

The Linux `.deb` and `.tar.gz` are built and tested in WSL by `tools/build_linux.py`, which
`tools/release.py` calls.

## Release

```powershell
# bump the version in src/refrata/__init__.py, commit, add a CHANGELOG.md entry, then:
python tools/release.py --notes-file build/notes-X.Y.Z.md
# no app changes, only newer components (1.4.1 → 1.4.2):
python tools/release.py --bump
```

`tools/release.py`, in order:

1. Updates the bundled components: the latest VLC 3 from VideoLAN (`tools/vlc-version.txt`) and
   the latest yt-dlp and python-vlc 3 from PyPI (`requirements.txt`).
2. Builds the Windows app and installer, and the Linux packages in WSL.
3. Runs every suite against the packaged apps: unit, e2e, robustness, launch budgets, the Linux
   e2e suite and clean installs on Ubuntu 24.04 and Debian 13. If anything fails, **nothing is
   published** and the components roll back.
4. Signs every file with the update key (`.sig`), commits the tested versions, tags, pushes and
   publishes the GitHub release with the installers, checksums and signatures.

Installed copies then see the update, and the site's download button points to the new version.
`--keep-versions` publishes without touching the components; `--skip-build --skip-tests` reuses a
`dist/` you already tested; `--draft` prepares the release without publishing it.

## Update signing key

Since 1.4.5 the app installs only updates signed by the project key, so a compromised GitHub
account could not push an installer. The private key lives only in `.env` (git-ignored) as
`REFRATA_UPDATE_KEY`; the public key is `UPDATE_PUBLIC_KEY` in `src/refrata/updater.py`.

```powershell
python tools/update_key.py generate   # once: creates the key
python tools/update_key.py public     # prints the public key of the key in .env
```

Keep a copy of `.env` off this PC. Without it no release can be published that installed apps
accept, and `tools/release.py` refuses to publish.

## Bundled components

The libVLC version shipped in the app is pinned in `tools/vlc-version.txt`. The build downloads
that version's official zip from VideoLAN once (into `build/cache`), checks the SHA-256 VideoLAN
publishes, and bundles it, so every build carries the same engine whatever VLC the machine has.

```powershell
python tools/check_updates.py   # libVLC, yt-dlp and python-vlc: bundled vs latest
```

Every Monday `.github/workflows/watch-updates.yml` runs the same check and opens or updates an
issue labeled `atualizacoes` when something is behind. The next release picks the new versions up
once they pass the tests. A major libVLC version (VLC 4) changes the API and is never taken
automatically.

Between releases the app keeps yt-dlp current by itself (`src/refrata/ytdlp.py`): at most once a
day when site links are used, and right away when one fails, it fetches the newer version from
PyPI, checks its SHA-256 and uses it from `%LOCALAPPDATA%\Refrata\yt-dlp`. A copy that does not
load is discarded. It is turned off together with automatic update checks.

## Code signing

The build signs `Refrata.exe`, the installer and the uninstaller (SHA-256 with a timestamp) when
one of these is set (details in `tools/sign.py`):

| Variable | Certificate |
|---|---|
| `REFRATA_SIGN_AZURE` + `REFRATA_SIGN_AZURE_DLIB` | Azure Artifact Signing |
| `REFRATA_SIGN_PFX` (+ `REFRATA_SIGN_PFX_PASSWORD`) | a `.pfx` file |
| `REFRATA_SIGN_THUMBPRINT` | a certificate in the Windows store (USB token, HSM) |
| `REFRATA_SIGN_DEV=1` | a self-signed development certificate, to test the flow only |

```powershell
$env:REFRATA_SIGN_DEV = "1"
python tools/build.py --skip-build     # re-sign the existing folder and rebuild zip and installer
python tools/sign.py --verify dist\Refrata-Setup-1.0.0.exe
```

A development certificate does not silence SmartScreen; that takes a certificate from a
certificate authority, or the Microsoft Store, where Microsoft signs the package. The SignPath
Foundation application is described in [code-signing.md](code-signing.md).

## Translations

Portuguese is the source language. The other 13 live in `src/refrata/locales/<code>.json`.

```powershell
python tools/i18n.py check     # coverage, placeholders, tags and plural forms in every language
```

## Logs

`%LOCALAPPDATA%\Refrata\` holds `crash.log` (fatal errors), `native.log` (libVLC and FFmpeg
messages), `opening.log` (the time of each step when a file opens) and, in the packaged app,
`stdout.log`.
