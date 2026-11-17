import QtQuick

// player.time arrives from the engine's 120 ms poll; progress bars driven by it step ~8×/s.
// This clock glides between polls while playing and snaps on seeks/track changes.
//
// It steps with the video's own pictures (frameSource: the video sink on screen), which are drawn
// anyway, and on a 30 Hz timer only when no picture comes (music, a hidden video). Stepping every
// display refresh (FrameAnimation) made the whole window redraw 165 times a second on a 165 Hz
// screen for a 30 fps video: about 2 CPU cores for a progress bar.
QtObject {
    id: root
    property bool active: true
    property QtObject frameSource: null   // a QVideoSink; null = timer only
    property real value: 0
    property real _base: 0
    property double _stamp: 0
    property double _lastFrame: 0
    // copies, read again only when the engine says they changed: the per-step code below must
    // not call into Python (each call may wait for the GIL, and the screen waits for this thread)
    readonly property real _rate: player.rate
    readonly property int _length: player.length
    readonly property bool _playing: player.playing

    function sync() {
        var t = player.time
        if (!player.playing || Math.abs(t - value) > 700) value = t
        _base = t
        _stamp = Date.now()
    }
    function step() {
        var est = _base + (Date.now() - _stamp) * _rate
        est = Math.min(est, _base + 400)          // never run far ahead of the engine
        if (_length > 0) est = Math.min(est, _length)
        // monotonic: tiny backward corrections are absorbed instead of jittering the bar
        if (est > value) value = est
    }
    Component.onCompleted: sync()

    property Connections _c: Connections {
        target: player
        function onTimeChanged() { root.sync() }
        function onStateChanged() { root.sync() }
        // a new file starts the clock over; the same file announced again (its title arrived, it
        // turned out to be sound only) keeps gliding: snapping there made the bar jump back
        function onMediaChanged() {
            if (player.currentPath !== root._path) {
                root._path = player.currentPath
                root.value = player.time
            }
            root.sync()
        }
    }
    property string _path: ""
    property Connections _frames: Connections {
        target: root.frameSource
        ignoreUnknownSignals: true
        function onVideoFrameChanged() {
            root._lastFrame = Date.now()
            if (root.active && root._playing) root.step()
        }
    }
    property Timer _t: Timer {
        interval: 33
        repeat: true
        running: root.active && root._playing
        // a value that does not change draws nothing: with pictures coming this is a no-op
        onTriggered: if (Date.now() - root._lastFrame > 150) root.step()
    }
}
