import QtQuick

// player.time arrives from the engine's 120 ms poll; progress bars driven by it step ~8×/s.
// This clock glides between polls every frame while playing and snaps on seeks/track changes.
QtObject {
    id: root
    property bool active: true
    property real value: 0
    property real _base: 0
    property double _stamp: 0

    function sync() {
        var t = player.time
        if (!player.playing || Math.abs(t - value) > 700) value = t
        _base = t
        _stamp = Date.now()
    }
    Component.onCompleted: sync()

    property Connections _c: Connections {
        target: player
        function onTimeChanged() { root.sync() }
        function onStateChanged() { root.sync() }
        function onMediaChanged() { root.value = player.time; root.sync() }
    }
    property FrameAnimation _f: FrameAnimation {
        running: root.active && player.playing
        onTriggered: {
            var est = root._base + (Date.now() - root._stamp) * player.rate
            est = Math.min(est, root._base + 400)          // never run far ahead of the engine
            if (player.length > 0) est = Math.min(est, player.length)
            // monotonic: tiny backward corrections are absorbed instead of jittering the bar
            if (est > root.value) root.value = est
        }
    }
}
