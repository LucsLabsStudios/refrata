#version 440
// Motion search, one level of a coarse-to-fine pyramid (VideoPipeline.qml runs it 4 times).
//
// Bilateral matching: a vector v at point p pairs older(p - v/2) with newer(p + v/2), so the field
// is anchored on the picture halfway between the two frames. Compensation then never leaves holes
// or doubles content (each output pixel owns its vector), unlike a forward search from the older
// frame. Every fragment is one block; its 4x4 samples are read from the mip level whose texels are
// a quarter of the block, so each sample already averages its area (robust to noise and grain).
//
// Seeds: zero, the coarser level's vector here and at its four neighbours (all of them, when this
// is the first level, replaced by a full search). Around the best seed, a square search of
// `radius` steps. A small penalty for leaving the seed keeps the field smooth on flat areas.
//
// Output: xy = vector in source pixels (newer - older), z = match error (mean |Δluma|), w = 1.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 srcPx;        // size of the captured frames, in pixels
    float blockPx;     // block side at this level, in pixels
    float lod;         // mip level whose texel is blockPx / 4
    float radius;      // search half-width, in steps
    float stepPx;      // distance between candidates, in pixels
    float first;       // 1 = coarsest level: full search around zero, no seeds
    float lambda;      // smoothness penalty per pixel of distance from the seed
};
layout(binding = 1) uniform sampler2D older;
layout(binding = 2) uniform sampler2D newer;
layout(binding = 3) uniform sampler2D seeds;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

float luma(sampler2D s, vec2 px) {
    return dot(textureLod(s, px / srcPx, lod).rgb, LUMA);
}

float cost(vec2 c, vec2 v) {
    float sad = 0.0;
    vec2 h = 0.5 * v;
    for (int j = 0; j < 4; ++j) {
        for (int i = 0; i < 4; ++i) {
            vec2 o = (vec2(float(i), float(j)) - 1.5) * (blockPx * 0.25);
            sad += abs(luma(older, c + o - h) - luma(newer, c + o + h));
        }
    }
    return sad * (1.0 / 16.0);
}

void main() {
    vec2 c = qt_TexCoord0 * srcPx;
    vec2 seed = vec2(0.0);
    float best = cost(c, seed);
    if (first < 0.5) {
        // the coarser field here and one coarse block away on each side
        vec2 d = vec2(2.0 * blockPx, 0.0) / srcPx;
        vec2 cand[5];
        cand[0] = texture(seeds, qt_TexCoord0).xy;
        cand[1] = texture(seeds, qt_TexCoord0 + d.xy).xy;
        cand[2] = texture(seeds, qt_TexCoord0 - d.xy).xy;
        cand[3] = texture(seeds, qt_TexCoord0 + d.yx).xy;
        cand[4] = texture(seeds, qt_TexCoord0 - d.yx).xy;
        for (int k = 0; k < 5; ++k) {
            float e = cost(c, cand[k]);
            if (e < best) { best = e; seed = cand[k]; }
        }
    }
    vec2 bestV = seed;
    int r = int(radius);
    for (int y = -r; y <= r; ++y) {
        for (int x = -r; x <= r; ++x) {
            if (x == 0 && y == 0) continue;
            vec2 v = seed + vec2(float(x), float(y)) * stepPx;
            float e = cost(c, v) + lambda * length(v - seed) / max(blockPx, 1.0);
            if (e < best) { best = e; bestV = v; }
        }
    }
    fragColor = vec4(bestV, cost(c, bestV), 1.0);
}
