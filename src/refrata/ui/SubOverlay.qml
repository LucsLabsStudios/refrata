import QtQuick

// Subtitles drawn by the interface (refrata/tools.py cues): the second subtitle, at the top of the
// picture so it never covers libVLC's own at the bottom, or the subtitles made from the sound
// (aisubs.py), at the bottom. The cue on screen is looked up here, against a clock that already
// runs every frame (SmoothTime): no call into Python per frame.
Item {
    id: root
    property var cues: []          // [[start ms, end ms, text], ...] sorted by start
    property real time: 0
    property bool atTop: true
    property real fontScale: 1
    readonly property int index: find(time)
    readonly property string current: index >= 0 ? cues[index][2] : ""

    function find(t) {
        var lo = 0, hi = cues.length - 1, best = -1
        while (lo <= hi) {
            var mid = (lo + hi) >> 1
            if (cues[mid][0] <= t) { best = mid; lo = mid + 1 } else hi = mid - 1
        }
        // overlapping cues: the latest that started and has not ended
        for (var i = best; i >= 0 && i > best - 4; i--)
            if (cues[i][1] > t) return i
        return -1
    }

    Text {
        id: line
        objectName: "subOverlayText"
        visible: root.current !== ""
        text: root.current
        width: Math.min(root.width - 48, implicitWidth)
        anchors.horizontalCenter: parent.horizontalCenter
        y: root.atTop ? root.height * 0.06 : root.height * 0.88 - height
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        color: "white"
        style: Text.Outline
        styleColor: Qt.rgba(0, 0, 0, 0.85)
        font.pixelSize: Math.max(16, Math.round(root.height * 0.042 * root.fontScale))
        font.weight: Font.DemiBold
    }
}
