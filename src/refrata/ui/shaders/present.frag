#version 440
// What reaches the screen when the video pipeline is on (VideoPipeline.qml), once per display
// refresh: the in-between picture, HDR brought to the screen, then the light boost.
//
// Motion: the field (motion.frag + vecmedian.frag) gives, for each point of the picture halfway
// between `older` and `newer`, how far content moved. At `phase` t the point takes older from
// p - t·v and newer from p + (1-t)·v, mixed by t. Where the two disagree (an occluded area, a wrong
// vector) the nearest real frame shows instead, uncompensated: judder in one spot, never smeared
// garbage. A scene cut shows the nearest frame everywhere.
//
// HDR (hdr = 1 PQ, 2 HLG): the frames arrive as BT.2020 R'G'B' still in their transfer curve (Qt
// only applied the YCbCr matrix, into a 16-bit float texture). Here, to light (nits), then:
//
//  - how bright this scene really is: the HDR10+ scene peak when the file has HDR10+ (scenePeak),
//    else the peak measured on the GPU (peak.frag, `measured`), never above the file's own peak
//  - tone mapping onto the target: the HDR10+ Bézier curve when its intended display is close to
//    ours, else BT.2390's EETF, both on max(R,G,B) so hue and saturation stay
//  - SDR screen (hdrOut = 0): target 203 nits = white (BT.2408); BT.2020 to BT.709 with
//    out-of-gamut colours desaturated toward their own luminance; sRGB; dither for 8 bits
//  - HDR screen (hdrOut = 1, Windows HDR on, scRGB swapchain): target = the display's own peak, so
//    HDR plays at its real brightness. Values are in units of the desktop's SDR white (outWhite
//    nits), in the extended sRGB curve; the window's HDR layer (Main.qml) turns everything,
//    interface and video alike, into scRGB light. Colours outside BT.709 stay as negative values:
//    scRGB shows the whole BT.2020 gamut the panel can.
//
// Light boost (Splash's "Light Boost", done locally): darker neighbourhoods are lifted more than
// bright ones, local contrast against the area's mean, a little saturation to keep colour.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 srcPx;        // size of the captured frames, in pixels
    float phase;       // 0 = older, 1 = newer
    float interp;      // 1 = motion-compensate between older and newer
    float hdr;         // 0 SDR, 1 PQ (HDR10), 2 HLG
    float srcPeak;     // brightest the file says it gets, nits (MaxCLL, mastering peak, or 1000)
    float light;       // light boost, 0..1
    float meanLod;     // mip level for the light boost's neighbourhood mean
    vec2 fieldPx;      // size of the motion field, in blocks
    float hdrOut;      // 1 = HDR screen: scRGB output in units of outWhite
    float outWhite;    // nits of the desktop's SDR white (Windows' "SDR content brightness")
    float outPeak;     // the HDR screen's peak, nits
    float useMeasured; // 1 = scene peak from `measured` (peak.frag)
    float scenePeak;   // HDR10+ peak of the current scene, nits (0 = none)
    float curveOn;     // 1 = use the HDR10+ Bézier curve below
    float curveSrc;    // nits that map to the curve's input 1.0
    float curveDst;    // nits of the curve's output 1.0 (its targeted display)
    vec4 knee;         // x, y = knee point; z = number of anchors
    vec4 anc0;         // anchors 1..15 (0..1), four per vector
    vec4 anc1;
    vec4 anc2;
    vec4 anc3;
};
layout(binding = 1) uniform sampler2D older;
layout(binding = 2) uniform sampler2D newer;
layout(binding = 3) uniform sampler2D field;
layout(binding = 4) uniform sampler2D measured;

const vec3 LUMA709 = vec3(0.2126, 0.7152, 0.0722);
const vec3 LUMA2020 = vec3(0.2627, 0.6780, 0.0593);
const float SDR_WHITE = 203.0;

// ---------------------------------------------------------------- PQ (SMPTE ST 2084)
const float M1 = 0.1593017578125, M2 = 78.84375;
const float C1 = 0.8359375, C2 = 18.8515625, C3 = 18.6875;

vec3 pqToNits(vec3 e) {
    vec3 p = pow(clamp(e, 0.0, 1.0), vec3(1.0 / M2));
    return 10000.0 * pow(max(p - C1, 0.0) / (C2 - C3 * p), vec3(1.0 / M1));
}
float nitsToPq(float y) {
    float l = pow(clamp(y / 10000.0, 0.0, 1.0), M1);
    return pow((C1 + C2 * l) / (1.0 + C3 * l), M2);
}
float pqToNits1(float e) {
    float p = pow(clamp(e, 0.0, 1.0), 1.0 / M2);
    return 10000.0 * pow(max(p - C1, 0.0) / (C2 - C3 * p), 1.0 / M1);
}

// ---------------------------------------------------------------- HLG (ARIB STD-B67, BT.2100)
vec3 hlgToNits(vec3 e, float lw) {
    const float a = 0.17883277, b = 0.28466892, c = 0.55991073;
    e = clamp(e, 0.0, 1.0);
    vec3 lo = e * e / 3.0;
    vec3 hi = (exp((e - c) / a) + b) / 12.0;
    vec3 scene = mix(lo, hi, step(vec3(0.5), e));
    // OOTF for a display of peak lw: system gamma 1.2 at 1000 nits, BT.2100's formula elsewhere
    float gamma = 1.2 + 0.42 * log(lw / 1000.0) / log(10.0);
    float ys = max(dot(scene, LUMA2020), 1e-6);
    return lw * pow(ys, gamma - 1.0) * scene;
}

// ---------------------------------------------------------------- BT.2390 EETF
float eetf(float nits, float peak, float target) {
    // in PQ space, source [0, peak] onto target [0, target]
    float sp = nitsToPq(peak);
    float e = nitsToPq(nits) / sp;
    float maxLum = nitsToPq(target) / sp;
    float ks = 1.5 * maxLum - 0.5;
    if (e > ks) {
        float t = (e - ks) / (1.0 - ks);
        float t2 = t * t, t3 = t2 * t;
        e = (2.0 * t3 - 3.0 * t2 + 1.0) * ks + (t3 - 2.0 * t2 + t) * (1.0 - ks)
          + (-2.0 * t3 + 3.0 * t2) * maxLum;
    }
    return pqToNits1(e * sp);
}

// ---------------------------------------------------------------- HDR10+ (ST 2094-40) curve
float anchor(int i) {
    vec4 v = i < 4 ? anc0 : i < 8 ? anc1 : i < 12 ? anc2 : anc3;
    int k = i - 4 * (i / 4);
    return k == 0 ? v.x : k == 1 ? v.y : k == 2 ? v.z : v.w;
}
// linear up to the knee point, then a Bézier curve of order N = anchors + 1 from the knee to
// (1, 1); its inner control points are the anchors
float bezier(float x) {
    float kx = knee.x, ky = knee.y;
    x = clamp(x, 0.0, 1.0);
    if (x <= kx) return kx > 0.0 ? x * ky / kx : 0.0;
    float t = (x - kx) / max(1.0 - kx, 1e-6);
    int n = int(knee.z) + 1;
    // de Casteljau-free Bernstein sum: P0 = 0, P1..Pn-1 = anchors, Pn = 1
    float s = 0.0, binom = 1.0;
    for (int p = 0; p <= 16; ++p) {
        if (p > n) break;
        float P = p == 0 ? 0.0 : p == n ? 1.0 : anchor(p - 1);
        s += binom * pow(t, float(p)) * pow(1.0 - t, float(n - p)) * P;
        binom = binom * float(n - p) / float(p + 1);
    }
    return ky + (1.0 - ky) * s;
}

// the light this scene reaches, nits
float sceneLight() {
    float peak = max(srcPeak, 100.0);
    if (scenePeak > 0.0) return clamp(scenePeak, 100.0, max(peak, scenePeak));
    if (useMeasured > 0.5) {
        vec4 m = texture(measured, vec2(0.5));
        if (m.a > 0.5) return clamp(pqToNits1(m.r), 100.0, peak);
    }
    return peak;
}

// HDR R'G'B' -> light (BT.709 primaries, in units of `white`), tone mapped for the screen
vec3 hdrToLinear(vec3 c, float white) {
    float target = hdrOut > 0.5 ? max(outPeak, white) : SDR_WHITE;
    // HDR screen: the video's reference white (BT.2408: 203 nits) goes to the desktop's own white
    // when that is brighter. Shown at its absolute nits under a desktop at 240, an HDR film's white
    // sat at 0.85 of the interface's: HDR came out dimmer than the menus around it. Only the
    // highlights above that white then use the screen's remaining headroom, up to its peak.
    float gain = hdrOut > 0.5 ? max(outWhite / SDR_WHITE, 1.0) : 1.0;
    vec3 nits;
    if (hdr < 1.5) {
        nits = pqToNits(c) * gain;
        float peak = sceneLight() * gain;
        float m = max(max(nits.r, nits.g), nits.b);
        if (m > 1e-4) {
            float m2 = m;
            if (curveOn > 0.5)
                m2 = min(bezier(m / gain / curveSrc) * curveDst * gain, target);
            else if (peak > target * 1.01)
                m2 = eetf(min(m, peak), peak, target);
            else
                m2 = min(m, target);
            nits *= m2 / m;
        }
    } else {
        // HLG is relative to the display: shown for a display of the target's peak
        nits = hlgToNits(c, hdrOut > 0.5 ? clamp(outPeak, 400.0, 2000.0) : 1000.0);
        if (hdrOut < 0.5) {
            float m = max(max(nits.r, nits.g), nits.b);
            if (m > 1e-4) nits *= eetf(m, 1000.0, SDR_WHITE) / m;
        } else {
            float m = max(max(nits.r, nits.g), nits.b);
            float top = clamp(outPeak, 400.0, 2000.0);
            if (m > 1e-4 && gain > 1.0) nits *= eetf(min(m * gain, top * gain), top * gain, target) / m;
        }
    }
    // BT.2020 -> BT.709 primaries (linear light)
    return mat3(1.6605, -0.1246, -0.0182,
                -0.5876, 1.1329, -0.1006,
                -0.0728, -0.0083, 1.1187) * (nits / white);
}

vec3 srgbEncode(vec3 rgb) {
    return mix(rgb * 12.92, 1.055 * pow(rgb, vec3(1.0 / 2.4)) - 0.055, step(vec3(0.0031308), rgb));
}

vec3 hdrToSdr(vec3 c) {
    vec3 rgb = hdrToLinear(c, SDR_WHITE);
    // out of gamut: pull toward the colour's own luminance until it fits
    float y = dot(rgb, LUMA709);
    float lo = min(min(rgb.r, rgb.g), rgb.b);
    if (lo < 0.0) rgb = y + (rgb - y) * (y / max(y - lo, 1e-6));
    float hi = max(max(rgb.r, rgb.g), rgb.b);
    if (hi > 1.0) rgb /= hi;
    // sRGB, what the Windows desktop (and so the rest of the picture path) assumes
    return srgbEncode(clamp(rgb, 0.0, 1.0));
}

// HDR screen: light in units of the desktop white, written in the extended sRGB curve (sign kept,
// values above 1 allowed) so the window's HDR layer decodes video and interface the same way
vec3 hdrToScreen(vec3 c) {
    vec3 rgb = hdrToLinear(c, max(outWhite, 1.0));
    return sign(rgb) * srgbEncode(abs(rgb));
}

// ---------------------------------------------------------------- light boost
vec3 lightBoost(vec3 c, float mean, float k) {
    float y = dot(c, LUMA709);
    if (y <= 1e-4) return c;
    // lift: up to gamma 0.7 where the neighbourhood is dark, almost none where it is bright
    float g = 1.0 - 0.3 * k * (1.0 - smoothstep(0.08, 0.55, mean));
    float y1 = pow(y, g);
    // local contrast against the (lifted) neighbourhood mean
    float m1 = pow(max(mean, 1e-4), g);
    float y2 = y1 + 0.45 * k * (y1 - m1) * (1.0 - y1);
    // soft shoulder so highlights roll off instead of clipping
    y2 = y2 > 0.85 ? 0.85 + 0.15 * tanh((y2 - 0.85) / 0.15) : y2;
    y2 = clamp(y2, 0.0, 1.0);
    vec3 r = c * (y2 / y);
    r = mix(vec3(y2), r, 1.0 + 0.18 * k);
    float hi = max(max(r.r, r.g), r.b);
    return hi > 1.0 ? mix(vec3(y2), r, (1.0 - y2) / max(hi - y2, 1e-6)) : max(r, 0.0);
}

// interleaved gradient noise: cheap, well spread, no texture
float ign(vec2 p) { return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }

void main() {
    vec2 uv = qt_TexCoord0;
    vec4 c;
    if (interp > 0.5) {
        // per-pixel vector: the smoothly interpolated field (flow, like MVTools' FlowFPS), or,
        // where that blends two motions at an object's edge, the vector of this pixel's own block
        // or one of its four neighbours, whichever pairs older and newer best right here (checked
        // on a 2x2-averaged level, so noise does not decide). Bilinear vectors alone leave a halo
        // around moving objects; whole-block vectors alone leave blocky edges.
        vec4 f = texture(field, uv);
        vec2 best = f.xy;
        float bestE = 1e9;
        // block centres (no texelFetch on GLSL ES 2): a linear sample exactly at a texel centre
        // is that texel
        vec2 fb = clamp(floor(uv * fieldPx), vec2(0.0), fieldPx - 1.0);
        // last candidate: no motion. Wins only where both frames agree in place (a channel logo,
        // burnt-in subtitles, black bars, a static overlay), which a block vector dragged along
        // with the moving picture around it would otherwise smear (Splash protects such areas by
        // flood-filling unchanged blocks)
        for (int k = 0; k < 7; ++k) {
            vec2 cand;
            if (k == 0) cand = f.xy;
            else if (k == 6) cand = vec2(0.0);
            else {
                vec2 o = k == 1 ? vec2(0.0) : k == 2 ? vec2(1.0, 0.0) : k == 3 ? vec2(-1.0, 0.0)
                       : k == 4 ? vec2(0.0, 1.0) : vec2(0.0, -1.0);
                cand = textureLod(field, (clamp(fb + o, vec2(0.0), fieldPx - 1.0) + 0.5) / fieldPx, 0.0).xy;
            }
            vec2 cv = cand / srcPx;
            float e = abs(dot(textureLod(older, uv - phase * cv, 1.0).rgb
                            - textureLod(newer, uv + (1.0 - phase) * cv, 1.0).rgb, LUMA709));
            // the smooth field wins ties: it is right almost everywhere and never blocky
            e += k == 0 ? 0.0 : k == 6 ? 0.008 : 0.012;
            if (e < bestE) { bestE = e; best = cand; }
        }
        vec2 v = best / srcPx;
        vec4 a = texture(older, uv - phase * v);
        vec4 b = texture(newer, uv + (1.0 - phase) * v);
        vec4 mc = mix(a, b, phase);
        vec4 near = phase < 0.5 ? texture(older, uv) : texture(newer, uv);
        float d = abs(dot(a.rgb - b.rgb, LUMA709));
        float local = smoothstep(0.035, 0.14, max(d, 0.8 * f.z));
        // where the motion is not found, the two real frames blended in place (measured on a
        // 60 fps clip halved to 15: the worst 1% of pixels off by 80 levels, against 120 for the
        // nearest real frame; a hard switch to it also jumped at mid-phase)
        vec4 blend = mix(texture(older, uv), texture(newer, uv), phase);
        // scene cut: the whole field matches badly (mean error), or most blocks found no match
        // at all (share of changed blocks, MVTools' thscd2): the real frame, never a blend
        vec4 whole = textureLod(field, vec2(0.5), 16.0);
        float scene = max(smoothstep(0.10, 0.16, whole.z), smoothstep(0.45, 0.65, whole.w));
        c = mix(mix(mc, blend, local), near, scene);
    } else {
        c = texture(newer, uv);
    }
    vec3 rgb = c.a > 1e-4 ? c.rgb / c.a : vec3(0.0);
    bool toHdrScreen = hdr > 0.5 && hdrOut > 0.5;
    if (toHdrScreen) rgb = hdrToScreen(rgb);
    else if (hdr > 0.5) rgb = hdrToSdr(rgb);
    // the light boost works on SDR-range pictures (on an HDR screen, HDR video already has its
    // own highlights)
    if (light > 0.001 && !toHdrScreen) {
        vec4 m = textureLod(newer, uv, meanLod);
        vec3 mrgb = m.a > 1e-4 ? m.rgb / m.a : vec3(0.0);
        if (hdr > 0.5) mrgb = hdrToSdr(mrgb);
        rgb = lightBoost(rgb, dot(mrgb, LUMA709), light);
    }
    // dither for an 8-bit screen; the HDR layer is 16-bit float
    if ((hdr > 0.5 || light > 0.001) && hdrOut < 0.5)
        rgb += (ign(gl_FragCoord.xy) - 0.5) / 255.0;
    fragColor = vec4(rgb * c.a, c.a) * qt_Opacity;
}
