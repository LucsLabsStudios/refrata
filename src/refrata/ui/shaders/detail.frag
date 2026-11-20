#version 440
// Detail boost (Splash's "Detail Boost"): adaptive sharpening, run once per decoded frame (not per
// display refresh). Each pixel's brightness is pushed away from its 3x3 neighbourhood's, by an amount
// that shrinks where the neighbourhood is nearly flat (noise and compression blocks are not
// amplified) and on the hardest edges, and is held to a little beyond the neighbourhood's own range
// (no halos).
//
// The neighbours are one *video* pixel apart (radius, in screen pixels): measured one screen pixel
// apart, a 1080p film shown on a 4K window had its detail already smoothed by the upscale, and even
// 100% looked like nothing. On brightness only, so colour edges get no fringes.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 srcPx;
    float amount;      // 0..1
    float radius;      // screen pixels per video pixel (1 = the video at its own size)
};
layout(binding = 1) uniform sampler2D source;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

void main() {
    vec2 t = max(radius, 1.0) / srcPx;
    vec4 c = texture(source, qt_TexCoord0);
    float lc = dot(c.rgb, LUMA);
    float sum = 0.0, wsum = 0.0, mn = lc, mx = lc;
    for (int y = -1; y <= 1; ++y) {
        for (int x = -1; x <= 1; ++x) {
            if (x == 0 && y == 0) continue;
            float l = dot(texture(source, qt_TexCoord0 + vec2(float(x), float(y)) * t).rgb, LUMA);
            float w = (x == 0 || y == 0) ? 1.0 : 0.5;          // corners count half
            sum += l * w;
            wsum += w;
            mn = min(mn, l);
            mx = max(mx, l);
        }
    }
    float blur = sum / wsum;
    float range = mx - mn;
    // texture and ordinary edges: full strength; flat areas (noise): none; very hard edges: less
    float adapt = smoothstep(0.003, 0.02, range) * (1.0 - 0.6 * smoothstep(0.30, 0.75, range));
    float boosted = lc + (lc - blur) * (2.8 * amount * adapt);
    // a little beyond the neighbourhood's own range: crisp, no ringing
    boosted = clamp(boosted, mn - 0.45 * range, mx + 0.45 * range);
    // the same change on every channel, scaled to keep the colour
    vec3 r = lc > 1e-4 ? c.rgb * (max(boosted, 0.0) / lc) : c.rgb + (boosted - lc);
    // dark pixels: add instead of scale (a ratio there would blow up the colour)
    r = mix(c.rgb + (boosted - lc), r, smoothstep(0.02, 0.08, lc));
    fragColor = vec4(r, c.a);
}
