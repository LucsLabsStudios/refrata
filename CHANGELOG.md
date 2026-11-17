# Changelog

All notable changes to Refrata are listed here, newest first. Each version links to its
[GitHub release](https://github.com/LucsLabsStudios/refrata/releases), which carries the full notes
(in Portuguese), the installers and their checksums and signatures.

The project follows [Semantic Versioning](https://semver.org/). Patch releases (`1.4.x`) can also
ship updated components only (libVLC, yt-dlp, python-vlc) after passing the full test suite.

## [2.0.0] - 2026-11-17
### Added
- Smooth motion (Settings › Playback › Picture): in-between frames computed on the GPU, so 24 and
  30 fps video plays at 60, 120 or the monitor's own refresh rate (144, 165, 240 Hz...). Motion is
  searched coarse to fine with bilateral block matching down to a quarter of a pixel, cleaned by a
  vector median; each pixel then takes the best of the smooth field, its neighbouring blocks'
  vectors and no motion at all, so object edges stay clean and static logos, subtitles and black
  bars stay still. Occluded areas and scene cuts (judged by how many blocks found no match) fall
  back to the real frame instead of smearing. The sound is delayed by one source frame to stay in
  sync. No resolution limit.
- HDR10, HDR10+ and HLG play with correct colours and brightness: the colour description is read
  from the file (MP4 `colr`/`mdcv`/`clli`, Matroska `Colour`, HEVC VUI and SEI, MPEG-TS), 10-bit
  frames reach the GPU as P010, and a shader applies the BT.2390 tone curve, BT.2020 to BT.709
  gamut mapping and dithering. Snapshots of HDR video are converted too.
- Per-scene tone mapping: HDR10+ (SMPTE ST 2094-40) metadata is read from MP4 and Matroska files
  and followed scene by scene, including the film's own tone curve when it was made for a display
  like yours; plain HDR10 is measured on the GPU frame by frame, so dark scenes are no longer
  dimmed for highlights that never come.
- True HDR on HDR screens: with Windows HDR on, the window uses a scRGB swapchain and HDR video
  shows at its real brightness, up to the screen's peak; the interface keeps the desktop's SDR
  brightness.
- Light boost and detail boost: a local "HDR look" for SDR video and adaptive sharpening.
- NVIDIA RTX Video Super Resolution and RTX Video HDR (Settings › Picture, RTX GPUs): the picture
  goes through the GPU's video processor with NVIDIA's AI upscaler, straight to its size on screen;
  RTX Video HDR turns SDR video into HDR10 on an HDR screen. Needs the matching switch on in the
  NVIDIA App.
- Subtitles made from the sound (Tracks › More subtitles): speech recognition with OpenAI's
  Whisper models (whisper.cpp built into the app, on the GPU through Vulkan), optionally translated
  into English. Nothing leaves the computer; the model is downloaded once and checked by SHA-256.
- A second subtitle at the top of the picture, together with the usual one (two languages at once):
  SRT, WebVTT and ASS.
- Tools panel: one frame back (Shift+E), bookmarks per file shown on the timeline (B), skip the
  opening and ending of every video of a folder (series), and save the A-B part (or the next 6 s)
  as MP4 (streams copied: no quality lost, HDR kept) or as an animated GIF.
- Audio passthrough (Settings › Sound): Dolby and DTS (and in HD mode TrueHD, DTS-HD, Atmos) sent
  to the receiver or soundbar as they are.
### Performance
- Frames go from libVLC to the screen without Python: a Rust and C++ module (refrata_native) with
  reused, aligned memory, delivered to Qt in C++. 4K60 with another Python thread busy: from 39 to
  60 frames a second, from 106 dropped frames in 5 s to none.
- The UI thread no longer calls into Python per frame (progress clock, time and length bindings,
  theme colours): 6783 to about 2000 calls in 3 s of 4K60; pages built in the background wait
  while a video plays; HDR10+ is read in Rust.
- Opening a video while Refrata runs no longer shows the splash screen; it shows only on a cold
  start (a native window instead of the bootloader's).
### Changed
- Video above 4K plays at its own size: 8K (7680×4320) and DCI 8K are no longer scaled down.
### Fixed
- A video opened in the middle (resumed, or handed over from the quick player) no longer flashes
  the music player over it before its first picture. A link of unknown kind takes the music look
  only once libVLC finds no video track in it, about 2 s after its sound starts.

## [1.4.16] - 2026-11-15
### Fixed
- Settings: a second click on a picker (audio output, subtitle encoding) now closes its list
  instead of closing and reopening it at once.

## [1.4.15] - 2026-11-15
### Fixed
- Timeline hover preview: the preview decoder stays paused and checks the decoded timestamp, so
  the thumbnail shows the exact frame under the pointer.

## [1.4.14] - 2026-11-14
### Changed
- Timeline hover preview follows the video's aspect ratio (9:16, 1:1, 4:3, 16:9, 21:9), rotation,
  mirroring and forced aspect.

## [1.4.13] - 2026-11-14
### Changed
- Timeline hover preview builds its storyboard progressively in the background for near-instant
  response, and idles when nothing plays.

## [1.4.12] - 2026-11-13
### Added
- Scene preview when hovering the timeline of local videos.
### Changed
- Faster menu, panel and toast animations; glass capture and music backdrop pause when hidden.

## [1.4.10] - 2026-11-12
### Fixed
- Opening a video while Refrata runs took 2–5 s when the history held files on slow drives;
  now 0.6–0.8 s.
- The resume position of the previous video was saved under the next file's name.
- Pausing by clicking the video while it was still loading did nothing.
- Linux: MPRIS sent the track length with the wrong D-Bus type.
- Windows 11: the default-app check read an outdated registry location.
### Added
- Settings › Default player lists which file types still open in another app.
- `opening.log` records the time each opening step takes.

## [1.4.9] - 2026-11-12
### Fixed
- Switching videos from the tray took about a second with the UI blocked; now ~0.3 s.
- The window could open behind others when a file was opened from Explorer.
- Several playback, Explorer and update-check edge cases.
### Tests
- All 191 interface controls are exercised by a test with real mouse clicks.

## [1.4.8] - 2026-11-11
### Changed
- A video opened with Refrata closed starts from its first frame when the window appears.
### Security
- XSPF playlists with XML tricks are refused; oversized playlists are not read whole.
- yt-dlp self-updates only over HTTPS from PyPI.

## [1.4.7] - 2026-11-10
### Fixed
- Green picture after a hardware-decoded video.
- Quick preview keyboard focus, window restore after PiP or tray, and a 0:00 flash on resume.

## [1.4.6] - 2026-11-10
### Added
- Live scrubbing: the picture follows the timeline while dragging.
### Fixed
- Linux: X11 sessions missing two system libraries, and black video on Debian 13.
- Audio device listing no longer blocks the window.

## [1.4.5] - 2026-11-10
### Added
- Linux packages (`.deb` for Ubuntu 24.04, Debian 13, Linux Mint 22 and newer, plus `.tar.gz`)
  with MPRIS media keys and desktop integration.
- Settings split into tabs; audio output and subtitle encoding became pickers.
### Changed
- Much faster opening from Explorer, single-instance hand-off in about 0.2 s.
### Security
- Updates must carry an Ed25519 signature from the project key.

## [1.4.4] - 2026-11-09
### Changed
- Hardware decoding now actually applies: about 70% less CPU on 4K video.
### Fixed
- Picture-in-picture disappearing on minimize, audio track switching on web links, a rare crash
  on exit.

## [1.4.3] - 2026-11-09
### Added
- Custom keyboard shortcuts; the portable build can register itself as default player; a launch
  splash screen.
### Changed
- Less CPU when idle; the Space preview watches the keyboard only while Explorer is in front.

## [1.4.2] - 2026-11-09
### Added
- yt-dlp keeps itself up to date (checked against PyPI's SHA-256).
### Changed
- libVLC always comes from the official VideoLAN package, verified by SHA-256.

## [1.4.1] - 2026-11-09
### Added
- Picture-in-picture (`Ctrl+P`).
- Immediate "opening link" feedback with a Cancel button.

## [1.4.0] - 2026-11-09
### Added
- Native-resolution 4K with GPU color conversion; rotate, mirror, aspect, zoom, deinterlace.
- Volume normalization and audio output selection.
- OpenSubtitles search, subtitle size, color, background and encoding.
- Chapters, YouTube and other site links (yt-dlp), Chromecast, Windows media keys.

## [1.3.1] - 2026-11-08
### Changed
- "Open files in the quick player" is back as an option, off by default.
### Fixed
- A crash on exit after an audio device change, and a rare thumbnail crash.

## [1.3.0] - 2026-11-08
### Added
- 14 interface languages, switched live; the installer follows the Windows language.

## [1.2.3] - 2026-11-07
### Fixed
- Quick player tooltips clipped by the rounded window edge.

## [1.2.2] - 2026-11-07
### Fixed
- Quick player behaviour with Refrata in the tray.

## [1.2.1] - 2026-11-07
### Fixed
- Files opened from Explorer while Refrata is open now play in the main window.

## [1.2.0] - 2026-11-07
### Added
- Space preview in Explorer (QuickLook-style), quick player, background mode, A-B repeat,
  always on top.

## [1.1.3] - 2026-11-07
### Changed
- Updating over an existing install is about 10× faster.

## [1.1.2] - 2026-11-07
### Fixed
- In-app updates from 1.1.0 and 1.1.1 failed on GitHub's download redirect.

## [1.1.1] - 2026-11-07
### Changed
- Reworked animations throughout; respects the Windows "show animations" setting.

## [1.1.0] - 2026-11-07
### Added
- First public, open-source release, with in-app updates verified by SHA-256.

[2.0.0]: https://github.com/LucsLabsStudios/refrata/releases/tag/v2.0.0
[1.4.16]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.16
[1.4.15]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.15
[1.4.14]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.14
[1.4.13]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.13
[1.4.12]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.12
[1.4.10]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.10
[1.4.9]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.9
[1.4.8]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.8
[1.4.7]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.7
[1.4.6]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.6
[1.4.5]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.5
[1.4.4]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.4
[1.4.3]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.3
[1.4.2]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.2
[1.4.1]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.1
[1.4.0]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.4.0
[1.3.1]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.3.1
[1.3.0]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.3.0
[1.2.3]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.2.3
[1.2.2]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.2.2
[1.2.1]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.2.1
[1.2.0]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.2.0
[1.1.3]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.1.3
[1.1.2]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.1.2
[1.1.1]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.1.1
[1.1.0]: https://github.com/LucsLabsStudios/refrata/releases/tag/v1.1.0
