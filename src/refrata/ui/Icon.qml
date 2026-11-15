import QtQuick

// icon glyph by name: Segoe Fluent Icons on Windows, the bundled Fluent UI System Icons elsewhere
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
        , "mail": "", "apps": "", "download": "", "sync": "", "pin": "", "loop": "", "cast": "", "rotate": "", "chapters": "", "pip": "", "pipexit": ""
    })
    // Fluent UI System Icons (MIT, assets/fonts): where Segoe Fluent Icons is missing (Linux, macOS)
    readonly property var fluent: ({
        "home": "\uF481", "video": "\uF84D", "music": "\uE855", "queue": "\uECDA", "globe": "\uF45B", "settings": "\uF6AA",
        "play": "\uF606", "pause": "\uF5A2", "prev": "\uF629", "next": "\uF56A", "back": "\uF15C", "down": "\uF2A4",
        "up": "\uF2B7", "left": "\uF2AB", "right": "\uF2B1", "fullscreen": "\uE685", "exitfull": "\uE688", "volume": "\uEB43",
        "mute": "\uEB4B", "vol1": "\uF6F9", "vol2": "\uF6FB", "vol3": "\uEB43", "cc": "\uF2E3", "speed": "\uF831",
        "eq": "\uF588", "camera": "\uF255", "shuffle": "\uEF37", "repeat": "\uF172", "repeat1": "\uEF34", "add": "\uF10A",
        "folder": "\uF419", "file": "\uF379", "search": "\uF690", "close": "\uF36A", "more": "\uE825", "timer": "\uF827",
        "delete": "\uF34D", "link": "\uF4E5", "brightness": "\uE1F8", "info": "\uF4A4", "list": "\uF4EE", "check": "\uF295",
        "refresh": "\uF13E", "sort": "\uF18B", "aspect": "\uF70B", "history": "\uF47F", "star": "\uF710", "playlist": "\uE010",
        "stop": "\uF72B", "headphones": "\uF472", "film": "\uEEFC", "drag": "\uE9F9", "theme": "\uF33C", "color": "\uF2F6",
        "explorer": "\uF42F", "subtitle": "\uEBCD", "picture": "\uF489", "clock": "\uF2DE", "audio": "\uE852", "mail": "\uF507",
        "apps": "\uF134", "download": "\uF151", "sync": "\uF191", "pin": "\uF602", "loop": "\uF172", "cast": "\uF26D",
        "rotate": "\uF186", "chapters": "\uE194", "pip": "\uF5FE", "pipexit": "\uE981"
    })
    text: (theme.iconSet === "fluent" ? fluent[name] : glyphs[name]) || ""
    font.family: theme.iconFont
    font.pixelSize: size
    color: theme.text
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
}
