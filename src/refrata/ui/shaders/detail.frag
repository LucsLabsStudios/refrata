#version 440
// Detail boost (Splash's "Detail Boost"): adaptive sharpening, run once per decoded frame (not per
// display refresh). Each pixel is pushed away from its 4 neighbours' mean, by an amount that
// shrinks where the neighbourhood already spans a wide range (edges stay clean, no halos) and
// where it is nearly flat (noise and compression blocks are not amplified).
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 srcPx;
    float amount;      // 0..1
};
layout(binding = 1) uniform sampler2D source;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

void main() {
    vec2 t = 1.0 / srcPx;
    vec4 c = texture(source, qt_TexCoord0);
    vec4 n = texture(source, qt_TexCoord0 + vec2(0.0, -t.y));
    vec4 s = texture(source, qt_TexCoord0 + vec2(0.0, t.y));
    vec4 e = texture(source, qt_TexCoord0 + vec2(t.x, 0.0));
    vec4 w = texture(source, qt_TexCoord0 + vec2(-t.x, 0.0));
    float lc = dot(c.rgb, LUMA), ln = dot(n.rgb, LUMA), ls = dot(s.rgb, LUMA);
    float le = dot(e.rgb, LUMA), lw = dot(w.rgb, LUMA);
    float mn = min(lc, min(min(ln, ls), min(le, lw)));
    float mx = max(lc, max(max(ln, ls), max(le, lw)));
    float range = mx - mn;
    // strongest on mid-contrast texture, fading on flat areas and hard edges
    float adapt = smoothstep(0.004, 0.03, range) * (1.0 - smoothstep(0.12, 0.45, range));
    vec3 hp = c.rgb - 0.25 * (n.rgb + s.rgb + e.rgb + w.rgb);
    vec3 r = c.rgb + hp * (1.6 * amount * adapt);
    // at most 30% beyond the neighbourhood's own range: crisp, without visible ringing
    vec3 lo = min(c.rgb, min(min(n.rgb, s.rgb), min(e.rgb, w.rgb)));
    vec3 hi = max(c.rgb, max(max(n.rgb, s.rgb), max(e.rgb, w.rgb)));
    r = clamp(r, lo - 0.3 * (hi - lo), hi + 0.3 * (hi - lo));
    fragColor = vec4(r, c.a);
}
