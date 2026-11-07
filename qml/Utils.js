.pragma library

function pad(n) { return n < 10 ? "0" + n : "" + n }

function time(ms) {
    if (!ms || ms < 0) ms = 0
    var s = Math.floor(ms / 1000)
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60
    return h > 0 ? h + ":" + pad(m) + ":" + pad(sec) : m + ":" + pad(sec)
}

function duration(ms) {
    if (!ms || ms <= 0) return ""
    var m = Math.round(ms / 60000)
    if (m < 1) return Math.max(1, Math.round(ms / 1000)) + " s"
    if (m < 60) return m + " min"
    return Math.floor(m / 60) + " h " + (m % 60 ? (m % 60) + " min" : "")
}

function greeting() {
    var h = new Date().getHours()
    if (h < 5) return "Boa noite"
    if (h < 12) return "Bom dia"
    if (h < 18) return "Boa tarde"
    return "Boa noite"
}

function today() {
    var d = new Date()
    var dias = ["Domingo", "Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado"]
    var meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                 "agosto", "setembro", "outubro", "novembro", "dezembro"]
    return dias[d.getDay()] + ", " + d.getDate() + " de " + meses[d.getMonth()]
}

function size(bytes) {
    if (bytes > 1073741824) return (bytes / 1073741824).toFixed(1) + " GB"
    if (bytes > 1048576) return Math.round(bytes / 1048576) + " MB"
    return Math.round(bytes / 1024) + " KB"
}

function ago(ts) {
    var d = Date.now() / 1000 - ts
    if (d < 60) return "agora"
    if (d < 3600) return Math.floor(d / 60) + " min atrás"
    if (d < 86400) return Math.floor(d / 3600) + " h atrás"
    if (d < 172800) return "ontem"
    return Math.floor(d / 86400) + " dias atrás"
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
