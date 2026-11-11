"""Windows media controls: the keyboard's play/pause/next/previous keys and the media overlay
(volume flyout, lock screen) show and control what Refrata plays.

Windows routes media keys to the "System Media Transport Controls" session of the app that
played last, so Refrata never steals the keys from another player the person is using.

The SMTC of a Windows.Media.Playback.MediaPlayer whose command manager is switched off is the
documented way for a desktop app to own a session without playing through that MediaPlayer:
sound keeps coming from libVLC; this object only mirrors title, cover, state and position.
Without the pywinrt packages (or on old Windows) this module does nothing.
"""
from __future__ import annotations

import os
import threading
from datetime import timedelta

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

try:
    from winrt.windows.foundation import Uri
    from winrt.windows.media import (MediaPlaybackStatus, MediaPlaybackType, SystemMediaTransportControlsButton,
                                     SystemMediaTransportControlsTimelineProperties)
    from winrt.windows.media.playback import MediaPlayer
    from winrt.windows.storage import StorageFile
    from winrt.windows.storage.streams import RandomAccessStreamReference
    AVAILABLE = True
except Exception:   # packages missing or Windows without WinRT media
    AVAILABLE = False


class MediaKeys(QObject):
    _button = Signal(int)
    _thumb = Signal(object, str)

    def __init__(self, engine, library, parent=None):
        super().__init__(parent)
        self._engine, self._library = engine, library
        self._smtc = None
        self._token = None
        self._shown = ("", "", "")
        self._art = ""
        self.available = False
        if not AVAILABLE or os.environ.get("REFRATA_NO_MEDIAKEYS") == "1":
            return
        try:
            self._mp = MediaPlayer()
            self._mp.command_manager.is_enabled = False   # we drive the session ourselves
            s = self._mp.system_media_transport_controls
            s.is_play_enabled = s.is_pause_enabled = s.is_stop_enabled = True
            s.is_next_enabled = s.is_previous_enabled = True
            s.is_enabled = False                          # nothing to show until something plays
            self._token = s.add_button_pressed(self._on_button)
            self._smtc = s
            self.available = True
        except Exception as exc:
            print("media keys unavailable:", exc)
            return
        self._button.connect(self._act)
        self._thumb.connect(self._set_thumb)
        engine.stateChanged.connect(self._sync_state)
        engine.currentIndexChanged.connect(self._sync_state)   # queue cleared → hide the session
        engine.mediaChanged.connect(self._sync_meta)
        # the overlay's progress bar: a refresh every couple of seconds and after seeks is plenty
        self._timeline = QTimer(self)
        self._timeline.setInterval(2000)
        self._timeline.timeout.connect(self._sync_timeline)
        self._timeline.start()

    # ------------------------------------------------------------ buttons (COM thread)
    def _on_button(self, sender, args):
        try:
            self._button.emit(int(args.button))
        except Exception as exc:
            print("media key:", exc)

    def _act(self, button: int):
        e = self._engine
        B = SystemMediaTransportControlsButton
        if button == int(B.PLAY):
            e.play()
        elif button == int(B.PAUSE):
            e.pause()
        elif button == int(B.STOP):
            e.pause()
        elif button == int(B.NEXT):
            e.next()
        elif button == int(B.PREVIOUS):
            e.previous()

    # ------------------------------------------------------------ mirror the player
    def _sync_state(self):
        s = self._smtc
        if s is None:
            return
        st = self._engine.state
        has = self._engine.hasMedia
        s.is_enabled = bool(has)
        s.playback_status = {
            "playing": MediaPlaybackStatus.PLAYING, "loading": MediaPlaybackStatus.CHANGING,
            "paused": MediaPlaybackStatus.PAUSED,
        }.get(st, MediaPlaybackStatus.STOPPED)
        self._sync_timeline()

    def _sync_meta(self):
        s = self._smtc
        if s is None or not self._engine.hasMedia:
            return
        e = self._engine
        title, artist, album = e.title or "", e.artist or "", e.album or ""
        if (title, artist, album) != self._shown:
            self._shown = (title, artist, album)
            u = s.display_updater
            u.clear_all()
            if e.isAudio:
                u.type = MediaPlaybackType.MUSIC
                u.music_properties.title = title
                u.music_properties.artist = artist
                u.music_properties.album_title = album
            else:
                u.type = MediaPlaybackType.VIDEO
                u.video_properties.title = title
            u.update()
        art = e.artwork or (self._library.thumbFor(e.currentPath) if self._library else "")
        if art != self._art:
            self._art = art
            self._load_thumb(art)
        self._sync_state()

    def _load_thumb(self, url: str):
        if not url:
            return
        if url.startswith("http"):
            self._set_thumb(RandomAccessStreamReference.create_from_uri(Uri(url)), url)
            return
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        if not os.path.isfile(path):
            return

        def work():   # StorageFile is async-only: resolve it off the UI thread
            import asyncio
            try:
                f = asyncio.run(_storage_file(os.path.normpath(path)))
                self._thumb.emit(RandomAccessStreamReference.create_from_file(f), url)
            except Exception as exc:
                print("media overlay cover:", exc)
        threading.Thread(target=work, daemon=True, name="smtc-cover").start()

    def _set_thumb(self, ref, url):
        if self._smtc is None or url != self._art:
            return   # a newer track already
        u = self._smtc.display_updater
        u.thumbnail = ref
        u.update()

    def _sync_timeline(self):
        s = self._smtc
        if s is None or not self._engine.hasMedia:
            return
        e = self._engine
        try:
            t = SystemMediaTransportControlsTimelineProperties()
            t.start_time = timedelta(0)
            t.min_seek_time = timedelta(0)
            t.position = timedelta(milliseconds=max(0, e.time))
            t.end_time = t.max_seek_time = timedelta(milliseconds=max(0, e.length))
            s.update_timeline_properties(t)
        except Exception as exc:
            print("media overlay timeline:", exc)

    # ------------------------------------------------------------ state for tests / shutdown
    def snapshot(self) -> dict:
        """What Windows shows right now (used by the test battery)."""
        s = self._smtc
        if s is None:
            return {}
        u = s.display_updater
        title = u.music_properties.title if u.type == MediaPlaybackType.MUSIC else u.video_properties.title
        return {"enabled": s.is_enabled, "status": int(s.playback_status), "title": title,
                "type": "music" if u.type == MediaPlaybackType.MUSIC else "video"}

    def shutdown(self):
        if self._smtc is None:
            return
        try:
            self._timeline.stop()
            self._smtc.is_enabled = False
            if self._token is not None:
                self._smtc.remove_button_pressed(self._token)
            self._mp.close()
        except Exception as exc:
            print("media keys shutdown:", exc)
        self._smtc = None


async def _storage_file(path):
    return await StorageFile.get_file_from_path_async(path)
