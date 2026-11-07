.pragma library

function pad(n) { return n < 10 ? "0" + n : "" + n }

function time(ms) {
    if (!ms || ms < 0) ms = 0
    var s = Math.floor(ms / 1000)
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
    return h > 0 ? h + ":" + pad(m) + ":" + pad(sec) : m + ":" + pad(sec)
}

// Text goes through qsTr so each language words it its own way; dates and numbers use the locale
// of the chosen language (QLocale.setDefault in refrata/i18n.py, read here by Qt.locale()).
function duration(ms) {
    if (!ms || ms <= 0) return ""
    var m = Math.round(ms / 60000)
    if (m < 1) return qsTr("%1 s").arg(Math.max(1, Math.round(ms / 1000)))
    if (m < 60) return qsTr("%1 min").arg(m)
    return m % 60 ? qsTr("%1 h %2 min").arg(Math.floor(m / 60)).arg(m % 60) : qsTr("%1 h").arg(Math.floor(m / 60))
}

function greeting() {
    var h = new Date().getHours()
    if (h < 5) return qsTr("Boa noite")
    if (h < 12) return qsTr("Bom dia")
    if (h < 18) return qsTr("Boa tarde")
    return qsTr("Boa noite")
}

function today() {
    // translators give the date pattern of their language (day and month names come from the locale)
    return new Date().toLocaleDateString(Qt.locale(), qsTr("dddd, d 'de' MMMM"))
}

function size(bytes) {
    var loc = Qt.locale()
    if (bytes > 1073741824) return qsTr("%1 GB").arg(Number(bytes / 1073741824).toLocaleString(loc, "f", 1))
    if (bytes > 1048576) return qsTr("%1 MB").arg(Math.round(bytes / 1048576))
    return qsTr("%1 KB").arg(Math.round(bytes / 1024))
}

function ago(ts) {
    var d = Date.now() / 1000 - ts
    if (d < 60) return qsTr("agora")
    if (d < 3600) return qsTr("há %n min", "", Math.floor(d / 60))
    if (d < 86400) return qsTr("há %n h", "", Math.floor(d / 3600))
    if (d < 172800) return qsTr("ontem")
    return qsTr("há %n dia(s)", "", Math.floor(d / 86400))
}

function fileUrl(p) {
    if (!p) return ""
    if (p.indexOf("://") > 0) return p
    return "file:///" + p.replace(/\\/g, "/")
}

function baseName(p) {
    var s = p.replace(/\\/g, "/")
    return s.substring(s.lastIndexOf("/") + 1)
}
