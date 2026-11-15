import QtQuick
import QtQuick.Shapes

// Circular-arrow "10" skip button (back / forward).
Item {
    id: root
    property bool forward: false
    property int seconds: 10
    property real size: 56
    property Item backdrop: null
    signal clicked()
    width: size; height: size

    GlassPanel {
        anchors.fill: parent
        backdrop: root.backdrop
        tint: ma.containsMouse ? Qt.rgba(1, 1, 1, 0.2) : Qt.rgba(0.1, 0.1, 0.12, 0.35)
        scale: ma.pressed ? 0.9 : 1
        Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }

        Item {
            anchors.centerIn: parent
            width: root.size * 0.5; height: width
            rotation: ma.pressed ? (root.forward ? 30 : -30) : 0
            Behavior on rotation { NumberAnimation { duration: 180; easing.type: Easing.OutBack } }
            transform: Scale { origin.x: root.size * 0.25; xScale: root.forward ? 1 : -1 }
            Shape {
                anchors.fill: parent
                // Qt 6.8's CurveRenderer leaks memory every time the shape changes (~0.3 MB per track
                // change in music mode): the geometry renderer, multisampled for smooth edges
                preferredRendererType: Shape.GeometryRenderer
                layer.enabled: true
                layer.samples: 4
                ShapePath {
                    strokeColor: "white"; strokeWidth: 2; fillColor: "transparent"; capStyle: ShapePath.RoundCap
                    startX: root.size * 0.25; startY: 1
                    PathArc { x: 1; y: root.size * 0.25; radiusX: root.size * 0.25 - 1; radiusY: root.size * 0.25 - 1; useLargeArc: true; direction: PathArc.Clockwise }
                }
                ShapePath {
                    strokeColor: "white"; strokeWidth: 2; fillColor: "transparent"
                    capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
                    startX: root.size * 0.18; startY: -3
                    PathLine { x: root.size * 0.25; y: 1 }
                    PathLine { x: root.size * 0.18; y: 5 }
                }
            }
        }
        Text {
            anchors.centerIn: parent
            anchors.verticalCenterOffset: 1
            text: root.seconds
            color: "white"
            font.pixelSize: root.size * 0.2
            font.weight: Font.Bold
        }
    }
    MouseArea { id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.clicked() }
}
