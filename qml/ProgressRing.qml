import QtQuick
import QtQuick.Shapes

// Circular progress arc (12 o'clock, clockwise) over a faint full track.
Item {
    id: root
    property real value: 0          // 0..1
    property real thickness: 3
    property color color: theme.accent
    property color trackColor: Qt.rgba(1, 1, 1, 0.14)
    readonly property real r: (Math.min(width, height) - thickness) / 2

    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: root.trackColor; strokeWidth: root.thickness
            fillColor: "transparent"; capStyle: ShapePath.RoundCap
            PathAngleArc { centerX: root.width / 2; centerY: root.height / 2; radiusX: root.r; radiusY: root.r; startAngle: 0; sweepAngle: 360 }
        }
        ShapePath {
            strokeColor: root.value > 0.001 ? root.color : "transparent"
            strokeWidth: root.thickness
            fillColor: "transparent"; capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: root.width / 2; centerY: root.height / 2; radiusX: root.r; radiusY: root.r
                startAngle: -90
                sweepAngle: 360 * Math.max(0, Math.min(1, root.value))
            }
        }
    }
    // the playhead: a bright dot at the arc's end
    Rectangle {
        readonly property real a: (-90 + 360 * Math.max(0, Math.min(1, root.value))) * Math.PI / 180
        visible: root.value > 0.001
        width: root.thickness * 2.4; height: width; radius: width / 2
        color: "white"
        x: root.width / 2 + root.r * Math.cos(a) - width / 2
        y: root.height / 2 + root.r * Math.sin(a) - height / 2
    }
}
