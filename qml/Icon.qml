import QtQuick

// Segoe Fluent Icons glyph by name
Text {
    id: root
    property string name: ""
    property real size: 18
    readonly property var glyphs: ({
        "home": "", "video": "", "music": "", "queue": "",
        "globe": "", "settings": "", "play": "", "pause": "",
        "prev": "", "next": "", "back": "", "down": "",
        "up": "", "left": "", "right": "", "fullscreen": "",
        "exitfull": "", "volume": "", "mute": "", "vol1": "",
        "vol2": "", "vol3": "", "cc": "", "speed": "",
        "eq": "", "camera": "", "shuffle": "", "repeat": "",
        "repeat1": "", "add": "", "folder": "", "file": "",
        "search": "", "close": "", "more": "", "timer": "",
        "delete": "", "link": "", "brightness": "", "info": "",
        "list": "", "check": "", "refresh": "", "sort": "",
        "aspect": "", "history": "", "star": "", "playlist": "",
        "stop": "", "headphones": "", "film": "", "drag": "",
        "theme": "", "color": "", "explorer": "", "subtitle": "",
        "picture": "", "clock": "", "audio": ""
        , "mail": "", "apps": "", "download": "", "sync": ""
    })
    text: glyphs[name] || ""
    font.family: theme.iconFont
    font.pixelSize: size
    color: theme.text
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
}
