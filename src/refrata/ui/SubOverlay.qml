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
    // the subtitle style chosen in the settings (engine.subtitleStyle), as libVLC's own subtitles
    // have it: size, colour, outline / box / nothing behind the text
    property var subStyle: ({})
    readonly property real styleScale: ({ small: 0.75, normal: 1, large: 1.35, huge: 1.75 })[subStyle.subSize || "normal"] || 1
    readonly property color textColor: subStyle.subColor === "yellow" ? "#FFE14D" : "white"
    readonly property string background: subStyle.subBackground || "outline"
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

    Rectangle {
        objectName: "subOverlayBox"
        visible: line.visible && root.background === "box"
        x: line.x + (line.width - line.contentWidth) / 2 - pad
        y: line.y - pad / 2
        width: line.contentWidth + 2 * pad
        height: line.contentHeight + pad
        radius: 4
        color: Qt.rgba(0, 0, 0, 0.67)
        readonly property real pad: Math.round(line.font.pixelSize * 0.35)
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
        color: root.textColor
        style: root.background === "outline" ? Text.Outline : Text.Normal
        styleColor: Qt.rgba(0, 0, 0, 0.85)
        font.pixelSize: Math.max(14, Math.round(root.height * 0.042 * root.fontScale * root.styleScale))
        font.weight: Font.DemiBold
    }
}
