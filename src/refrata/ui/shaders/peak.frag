#version 440
// Dynamic peak detection for HDR10 (VideoPipeline.qml): how bright the picture on screen really
// is, measured on the GPU, so a dark scene is not tone mapped as if the film's brightest highlight
// (MaxCLL) could be in it. Three passes, no read-back to the CPU:
//
//   stage 0  one texel per 32x32 pixels: the max and mean of max(R',G',B') over an 8x8 grid read
//            from the 4x4-averaged mip level (a lone hot pixel does not count as the peak)
//   stage 1  8x8 texels: the max and mean over a 16x16 grid of the stage-0 texture
//   stage 2  1x1 texel: the frame's max and mean, smoothed over time against its own previous
//            output (`prev`, a recursive ShaderEffectSource): brightness rises within a few
//            frames, falls slowly, and jumps at once on a scene cut (the mean changed a lot)
//
// Values stay in PQ code (0..1): the max of PQ values is the PQ of the max, and PQ is close to how
// we see brightness, so the smoothing and the scene-cut test behave alike in dark and bright
// scenes. Output: r = smoothed peak, g = mean, b = this frame's raw peak, a = 1 once valid.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float stage;       // 0, 1 or 2
    vec2 inPx;         // size of `source` in texels
};
layout(binding = 1) uniform sampler2D source;
layout(binding = 2) uniform sampler2D prev;

vec2 sample0(vec2 uv) {
    vec4 c = textureLod(source, uv, 2.0);
    vec3 v = c.a > 1e-4 ? c.rgb / c.a : vec3(0.0);
    float m = max(max(v.r, v.g), v.b);
    return vec2(m, m);
}

void main() {
    vec2 uv = qt_TexCoord0;
    float mx = 0.0, sum = 0.0;
    if (stage < 0.5) {
        // this texel covers 32x32 source pixels: an 8x8 grid, one sample per 4x4 pixels
        vec2 cell = 32.0 / inPx;
        vec2 base = floor(uv * inPx / 32.0) * cell;
        for (int j = 0; j < 8; ++j)
            for (int i = 0; i < 8; ++i) {
                vec2 p = base + (vec2(float(i), float(j)) + 0.5) * (cell / 8.0);
                vec2 s = sample0(min(p, vec2(1.0)));
                mx = max(mx, s.x);
                sum += s.y;
            }
        fragColor = vec4(mx, sum / 64.0, 0.0, 1.0);
    } else if (stage < 1.5) {
        // one eighth of the frame each way: a 16x16 grid over that region of stage 0
        vec2 base = floor(uv * 8.0) / 8.0;
        for (int j = 0; j < 16; ++j)
            for (int i = 0; i < 16; ++i) {
                vec2 p = base + (vec2(float(i), float(j)) + 0.5) / 128.0;
                vec4 s = textureLod(source, p, 0.0);
                mx = max(mx, s.r);
                sum += s.g;
            }
        fragColor = vec4(mx, sum / 256.0, 0.0, 1.0);
    } else {
        for (int j = 0; j < 8; ++j)
            for (int i = 0; i < 8; ++i) {
                vec4 s = textureLod(source, (vec2(float(i), float(j)) + 0.5) / 8.0, 0.0);
                mx = max(mx, s.r);
                sum += s.g;
            }
        float mean = sum / 64.0;
        vec4 p = texture(prev, vec2(0.5));
        float peak = mx;
        if (p.a > 0.5 && abs(mean - p.g) < 0.08) {
            // same scene: follow, quickly up (no clipped highlight for long), slowly down
            float k = mx > p.r ? 0.25 : 0.03;
            peak = p.r + (mx - p.r) * k;
        }
        fragColor = vec4(peak, mean, mx, 1.0);
    }
}
