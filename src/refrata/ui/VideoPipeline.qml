import QtQuick
import QtMultimedia

// The video picture, with the GPU work Refrata adds on top of libVLC (shaders in ui/shaders):
//
//  - smooth motion: in-between pictures at the display's own refresh rate (60, 120, 144, 240 Hz...),
//    from a motion field searched on the GPU, coarse to fine: blocks of 64, 32, 16 and 8 pixels,
//    then refined to half and quarter of a pixel (motion.frag), cleaned by a vector median that
//    also counts the blocks that found no match (vecmedian.frag); per pixel, the best of the
//    smooth field, the neighbouring block vectors and "no motion" (present.frag)
//  - HDR10 / HDR10+ / HLG: our own PQ/HLG decoding and tone mapping (present.frag), since Qt's own
//    HDR shaders break on out-of-gamut colours: per scene by HDR10+ metadata or by the brightness
//    measured on the GPU (peak.frag); onto an SDR screen, or at the real brightness of an HDR one
//    (hdrOut: the window's scRGB layer, refrata/hdroutput.py)
//  - light boost and detail boost (present.frag, detail.frag)
//
// With all of it off, this is one plain VideoOutput: nothing is captured or processed.
//
// Frames: with smooth motion on, the engine fills two sinks in turn (Engine.setPairSinks) and
// says which one is newer (pairFrame). The picture shown lags the newest frame by one source
// frame, so there is always a frame on each side of "now"; the engine delays the sound by the
// same amount (Engine.setPipelineDelay).
Item {
    id: vp

    property int fillMode: VideoOutput.PreserveAspectFit
    property int orientation: 0
    property bool smoothMotion: false  // smooth motion wanted
    property real maxFps: 0            // 0 = every display refresh
    property int hdr: 0                // 0 SDR, 1 PQ, 2 HLG
    property real srcPeak: 1000
    property real light: 0             // 0..1
    property real detail: 0            // 0..1
    property real sourceFps: 0         // the stream's frame rate × playback speed (0 = unknown)
    property bool playing: false
    property string outputName: "videoOutput"   // objectName of the plain output (tests find it)
    // HDR screen (hdroutput.py): the picture is written for the window's scRGB layer
    property bool hdrOut: false
    property real outWhite: 80         // nits of the desktop's SDR white
    property real outPeak: 0           // the screen's peak, nits
    // HDR10+ (hdr10plus.py): the current scene's metadata, {} when the file has none
    property var scene: ({})
    // measure each frame's brightness on the GPU (HDR10 without HDR10+)
    property bool measure: true
    readonly property real targetNits: hdrOut ? Math.max(outPeak, outWhite) : 203
    // NVIDIA RTX Video (native/src/rtx.cpp): the picture made by the GPU's video processor, with
    // Super Resolution (AI upscaling to the window) and/or TrueHDR (SDR made HDR10, for an HDR
    // screen). For SDR video shown straight (no rotation, no smooth motion: the processor makes
    // one picture per frame). TrueHDR's PQ output then goes through this pipeline as HDR10 does.
    property bool rtxVsr: false
    property bool rtxHdr: false
    property real router: 0          // the native frame path's router (player.nativeRouter)
    readonly property bool rtxWanted: (rtxVsr || (rtxHdr && hdrOut)) && hdr === 0 && !smoothMotion
                                      && orientation === 0 && fillMode === VideoOutput.PreserveAspectFit && router > 0
    readonly property bool rtxOn: rtxWanted && rtxLoader.status === Loader.Ready
    readonly property bool rtxHdrOn: rtxOn && rtxHdr && hdrOut
    readonly property int hdrIn: rtxHdrOn ? 1 : hdr
    readonly property var rtxItem: rtxLoader.item
    // what RTX Video really does now, for the interface (Settings, the Picture panel): it used to
    // stay off without a word for HDR video or with smooth motion on, and looked broken
    //   off | on | refused | hdrVideo | motion | transform | noScreen | starting
    readonly property string rtxState: {
        if (!(rtxVsr || rtxHdr)) return "off"
        if (hdr !== 0) return "hdrVideo"            // made for SDR video: HDR already is
        if (smoothMotion) return "motion"           // one picture per frame: no in-betweens
        if (orientation !== 0 || fillMode !== VideoOutput.PreserveAspectFit) return "transform"
        if (!rtxVsr && rtxHdr && !hdrOut) return "noScreen"
        if (!rtxOn || !rtxItem) return "starting"
        var s = rtxItem.status || ""
        if (s.indexOf("refused") >= 0 || s.indexOf("error") >= 0 || s.indexOf("unsupported") >= 0) return "refused"
        return s === "" ? "starting" : "on"
    }
    // smooth motion: off | on (in-betweens drawn) | idle (the video is already as fast as the screen)
    readonly property string motionState: !smoothMotion ? "off" : interpolating ? "on" : playing ? "idle" : "ready"
    readonly property real motionTo: Math.min(displayHz, maxFps > 0 ? maxFps : 1e9)
    Loader {
        id: rtxLoader
        anchors.fill: parent
        z: 1
        active: vp.rtxWanted
        source: "RtxLayer.qml"
        onLoaded: {
            item.router = Qt.binding(function () { return vp.router })
            item.superResolution = Qt.binding(function () { return vp.rtxVsr })
            item.trueHdr = Qt.binding(function () { return vp.rtxHdrOn })
        }
    }
    // the scene's own curve, when it was made for a display close to this one (within 25%)
    readonly property bool curveOn: hdr === 1 && !!scene.target && !!scene.anchors && scene.anchors.length > 0
                                    && Math.abs(targetNits - scene.target) <= 0.25 * scene.target

    readonly property alias output: voA
    readonly property alias videoSink: voA.videoSink
    // shows the processed picture (vs. the plain VideoOutput)
    readonly property bool active: smoothMotion || hdrIn > 0 || light > 0.001 || detail > 0.001
    readonly property bool pairing: smoothMotion
    // in-betweens are worth drawing only when the display is clearly faster than the source
    // the refresh rate of the screen the window is on, from Python (QML's Screen has no refresh
    // rate): read again when the item changes window or the window changes screen
    readonly property real displayHz: {
        var r = typeof appctl !== "undefined" ? appctl.refreshRate(Screen.name) : 0
        return r > 1 ? r : 60
    }
    readonly property bool interpolating: smoothMotion && playing && interval > 0
                                          && 1000 / interval < Math.min(displayHz, maxFps > 0 ? maxFps : 1e9) * 0.92
    readonly property real dpr: Screen.devicePixelRatio > 0 ? Screen.devicePixelRatio : 1
    readonly property size srcPx: Qt.size(Math.max(2, Math.round(width * dpr)), Math.max(2, Math.round(height * dpr)))

    // ---------------------------------------------------------------- timing
    property bool bNewer: false        // the newest frame is in voB
    property real phase: 1
    property real interval: sourceFps > 0 ? 1000 / sourceFps : _measured
    property real _measured: 0
    property real _arrival: 0          // smoothed arrival time of the newest frame (ms)
    property real _lastRaw: 0
    property int _frames: 0            // frames since the pair was (re)started

    signal pipelineDelay(real ms)
    onInterpolatingChanged: pipelineDelay(interpolating ? interval : 0)
    onIntervalChanged: if (interpolating) pipelineDelay(interval)

    function now() { return Date.now() }

    // the engine put a new frame in voB (slot 1) or voA (slot 0)
    function frameArrived(slot) {
        var t = now()
        var gap = t - _lastRaw
        _lastRaw = t
        bNewer = slot === 1
        if (_frames > 0 && gap > 0 && gap < 1000)
            _measured = _measured > 0 ? _measured * 0.9 + gap * 0.1 : gap
        var iv = interval
        // a stall, a seek or the first frame: show it as is and start the clock again
        if (_frames === 0 || iv <= 0 || gap > iv * 2.5) {
            _arrival = t
            phase = 1
        } else {
            // arrivals jitter with the UI thread; follow the steady beat instead (a slow PLL)
            var predicted = _arrival + iv
            var err = t - predicted
            _arrival = Math.abs(err) < iv * 0.5 ? predicted + err * 0.12 : t
            phase = 0
        }
        _frames++
        tick()
    }
    function restart() { _frames = 0; phase = 1 }
    property real phaseOverride: -1    // tests: a fixed phase
    function tick() {
        if (phaseOverride >= 0) { phase = phaseOverride; return }
        if (!interpolating) { phase = 1; return }
        var p = (now() - _arrival) / interval
        if (maxFps > 0) {
            // cap: whole steps of the chosen rate within each source frame
            var steps = Math.max(1, Math.round(interval * maxFps / 1000))
            p = Math.floor(p * steps) / steps
        }
        phase = Math.max(0, Math.min(1, p))
    }
    FrameAnimation {
        running: vp.interpolating && vp.visible
        onTriggered: vp.tick()
    }

    // ---------------------------------------------------------------- the two sinks
    VideoOutput {
        id: voA
        objectName: vp.outputName
        anchors.fill: parent
        visible: !vp.rtxOn          // the RTX item draws the picture instead
        fillMode: vp.fillMode
        orientation: vp.orientation
    }
    VideoOutput {
        id: voB
        objectName: vp.outputName + "B"
        anchors.fill: parent
        fillMode: vp.fillMode
        orientation: vp.orientation
        visible: vp.pairing
    }
    readonly property alias videoSinkB: voB.videoSink

    Loader {
        id: fx
        anchors.fill: parent
        active: vp.active && vp.visible
        sourceComponent: Item {
            id: g
            readonly property int fmt: vp.hdrIn > 0 ? ShaderEffectSource.RGBA16F : ShaderEffectSource.RGBA8

            ShaderEffectSource {
                id: capA
                sourceItem: vp.rtxOn ? vp.rtxItem : voA
                hideSource: true
                mipmap: true
                format: g.fmt
                textureSize: vp.srcPx
            }
            ShaderEffectSource {
                id: capB
                sourceItem: vp.pairing ? voB : null
                hideSource: true
                mipmap: true
                format: g.fmt
                textureSize: vp.srcPx
            }

            // detail boost, once per decoded frame
            component Detail: ShaderEffect {
                property variant source
                property size srcPx: vp.srcPx
                property real amount: vp.detail
                // screen pixels per video pixel: the sharpening works on the video's own detail
                property real radius: Math.max(1.0, Math.min(4.0, voA.contentRect.width * vp.dpr
                                                                    / Math.max(1, voA.sourceRect.width)))
                width: vp.width; height: vp.height
                visible: vp.detail > 0.001
                fragmentShader: "shaders/detail.frag.qsb"
            }
            Detail { id: detA; objectName: "detailA"; source: capA }
            Detail { id: detB; source: capB; visible: vp.detail > 0.001 && vp.pairing }
            ShaderEffectSource { id: sharpA; sourceItem: vp.detail > 0.001 ? detA : null; hideSource: true; mipmap: true; format: g.fmt; textureSize: vp.srcPx }
            ShaderEffectSource { id: sharpB; sourceItem: vp.detail > 0.001 && vp.pairing ? detB : null; hideSource: true; mipmap: true; format: g.fmt; textureSize: vp.srcPx }
            readonly property var srcA: vp.detail > 0.001 ? sharpA : capA
            readonly property var srcB: vp.detail > 0.001 ? sharpB : capB
            readonly property var newer: vp.pairing && vp.bNewer ? srcB : srcA
            readonly property var older: vp.pairing ? (vp.bNewer ? srcA : srcB) : srcA

            // motion field, coarse to fine: blocks of 64, 32, 16 and 8 pixels
            component Level: ShaderEffect {
                id: lv
                property real blockPx: 8
                property variant older: g.older
                property variant newer: g.newer
                property variant seeds
                property size srcPx: vp.srcPx
                property real lod: Math.max(0, Math.log(blockPx / 4) / Math.LN2)
                property real radius: 2
                property real stepPx: Math.max(1, blockPx / 8)
                property real first: 0
                property real lambda: 0.02
                width: vp.width; height: vp.height
                visible: vp.interpolating
                fragmentShader: "shaders/motion.frag.qsb"
                blending: false
            }
            component Field: ShaderEffectSource {
                property real blockPx: 8
                hideSource: true
                format: ShaderEffectSource.RGBA16F
                smooth: true
                textureSize: Qt.size(Math.ceil(vp.srcPx.width / blockPx), Math.ceil(vp.srcPx.height / blockPx))
            }
            Level { id: l64; blockPx: 64; first: 1; radius: 6; stepPx: 16; seeds: capA }
            Field { id: f64; blockPx: 64; sourceItem: vp.interpolating ? l64 : null }
            Level { id: l32; blockPx: 32; seeds: f64; radius: 2; stepPx: 6 }
            Field { id: f32; blockPx: 32; sourceItem: vp.interpolating ? l32 : null }
            Level { id: l16; blockPx: 16; seeds: f32; radius: 2; stepPx: 3 }
            Field { id: f16; blockPx: 16; sourceItem: vp.interpolating ? l16 : null }
            Level { id: l8; blockPx: 8; seeds: f16; radius: 2; stepPx: 1 }
            Field { id: f8; blockPx: 8; sourceItem: vp.interpolating ? l8 : null; smooth: false }
            // sub-pixel: the same blocks refined to half, then quarter of a pixel (bilinear
            // sampling of the 2x2-averaged level; MVTools needs pel=2 upsampled copies for this)
            Level { id: lh; blockPx: 8; seeds: f8; radius: 1; stepPx: 0.5 }
            Field { id: fh; blockPx: 8; sourceItem: vp.interpolating ? lh : null; smooth: false }
            Level { id: lq; blockPx: 8; seeds: fh; radius: 1; stepPx: 0.25 }
            Field { id: fq; blockPx: 8; sourceItem: vp.interpolating ? lq : null; smooth: false }
            ShaderEffect {
                id: med
                property variant field: fq
                property size fieldPx: fq.textureSize
                property real badErr: 0.06
                width: vp.width; height: vp.height
                visible: vp.interpolating
                fragmentShader: "shaders/vecmedian.frag.qsb"
                blending: false
            }
            Field { id: fmed; blockPx: 8; sourceItem: vp.interpolating ? med : null; mipmap: true }

            // dynamic peak (shaders/peak.frag): 32x32-pixel cells, then 8x8, then one smoothed value
            readonly property bool measuring: vp.measure && vp.hdr === 1 && !(vp.scene.peak > 0)
            component Peak: ShaderEffect {
                property variant source
                property variant prev: source
                property real stage: 0
                property size inPx: Qt.size(1, 1)
                visible: g.measuring
                blending: false
                fragmentShader: "shaders/peak.frag.qsb"
            }
            component PeakTex: ShaderEffectSource {
                hideSource: true
                format: ShaderEffectSource.RGBA16F
                smooth: false
            }
            Peak {
                id: pk0; source: g.newer; stage: 0; inPx: vp.srcPx
                width: Math.ceil(vp.srcPx.width / 32); height: Math.ceil(vp.srcPx.height / 32)
            }
            PeakTex { id: pk0s; sourceItem: g.measuring ? pk0 : null; textureSize: Qt.size(pk0.width, pk0.height) }
            Peak { id: pk1; source: pk0s; stage: 1; inPx: pk0s.textureSize; width: 8; height: 8 }
            PeakTex { id: pk1s; sourceItem: g.measuring ? pk1 : null; textureSize: Qt.size(8, 8) }
            Peak { id: pk2; source: pk1s; prev: pk2s; stage: 2; width: 1; height: 1 }
            // fed back into pk2: the previous frame's smoothed value
            PeakTex { id: pk2s; objectName: "videoPeak"; sourceItem: g.measuring ? pk2 : null; recursive: true; textureSize: Qt.size(1, 1) }

            ShaderEffect {
                id: present
                objectName: "videoPresent"
                anchors.fill: parent
                property variant older: g.older
                property variant newer: g.newer
                property variant field: vp.interpolating ? fmed : g.newer
                property size srcPx: vp.srcPx
                property real phase: vp.phase
                property real interp: vp.interpolating ? 1 : 0
                property real hdr: vp.hdrIn
                property real srcPeak: vp.srcPeak
                property real light: vp.light
                property real meanLod: Math.max(0, Math.log(Math.max(vp.srcPx.width, vp.srcPx.height) / 12) / Math.LN2)
                property size fieldPx: fmed.textureSize
                property real hdrOut: vp.hdrOut ? 1 : 0
                property real outWhite: vp.outWhite
                property real outPeak: vp.outPeak
                property variant measured: g.measuring ? pk2s : g.newer
                property real useMeasured: g.measuring ? 1 : 0
                property real scenePeak: vp.scene.peak > 0 ? vp.scene.peak : 0
                property real curveOn: vp.curveOn ? 1 : 0
                // ST 2094-40: the curve's input 1.0 is the scene's maxscl, its output 1.0 the
                // targeted display's peak (here: ours, within 25% of it)
                property real curveSrc: vp.scene.peak > 0 ? vp.scene.peak : vp.srcPeak
                property real curveDst: vp.targetNits
                readonly property var _a: vp.curveOn ? vp.scene.anchors : []
                function an(i) { return i < _a.length ? _a[i] : 1 }
                property vector4d knee: vp.curveOn ? Qt.vector4d(vp.scene.knee[0], vp.scene.knee[1], _a.length, 0) : Qt.vector4d(0, 0, 0, 0)
                property vector4d anc0: Qt.vector4d(an(0), an(1), an(2), an(3))
                property vector4d anc1: Qt.vector4d(an(4), an(5), an(6), an(7))
                property vector4d anc2: Qt.vector4d(an(8), an(9), an(10), an(11))
                property vector4d anc3: Qt.vector4d(an(12), an(13), an(14), 1)
                fragmentShader: "shaders/present.frag.qsb"
            }
        }
    }
}
