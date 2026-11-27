#version 440
// 3x3 vector median of the finest motion field: of the nine vectors around a block, keep the one
// closest to all the others. A lone wrong vector (a repeated texture matched one period off, a
// block on an edge) takes its neighbours' value; real motion boundaries stay sharp, unlike a blur.
// The match error (z) is the largest of the neighbourhood, so the edge of a moving object is
// treated as uncertain on both sides.
//
// w = how badly the area around this block matches, relative to its contrast: the mean difference
// of 5x5 samples over 1/16 of the width along the chosen vector, over how much those samples vary
// in either frame. Averaged over the whole field (the mip chain of the next pass) it is the
// scene-cut test. A fixed error limit missed cuts between dark shots, whose levels differ little;
// an area much larger than a block cannot be matched by chance. Black in both frames (letterbox
// bars) says nothing: it counts as `cutAt`, the value where present.frag decides.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 fieldPx;      // size of the field, in blocks
    float cutAt;       // where present.frag takes the field for a cut
    vec2 srcPx;        // size of the captured frames, in pixels
};
layout(binding = 1) uniform sampler2D field;
layout(binding = 2) uniform sampler2D older;
layout(binding = 3) uniform sampler2D newer;

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);

float mismatch(vec2 uv, vec2 v) {
    vec2 stepUv = vec2(1.0, srcPx.x / srcPx.y) / 80.0;
    float lod = max(0.0, log2(srcPx.x / 80.0) - 0.5);
    float a[25];
    float b[25];
    float ma = 0.0, mb = 0.0, d = 0.0;
    int n = 0;
    for (int j = -2; j <= 2; ++j)
        for (int i = -2; i <= 2; ++i) {
            vec2 p = clamp(uv + vec2(float(i), float(j)) * stepUv, vec2(0.0), vec2(1.0));
            a[n] = dot(textureLod(older, p - 0.5 * v, lod).rgb, LUMA);
            b[n] = dot(textureLod(newer, p + 0.5 * v, lod).rgb, LUMA);
            ma += a[n]; mb += b[n]; d += abs(a[n] - b[n]);
            ++n;
        }
    ma /= 25.0; mb /= 25.0;
    float va = 0.0, vb = 0.0;
    for (int k = 0; k < 25; ++k) { va += abs(a[k] - ma); vb += abs(b[k] - mb); }
    if (max(ma, mb) < 0.012) return cutAt;
    return min(d / (max(va, vb) + 0.02 * 25.0), 2.0);
}

void main() {
    vec2 t = 1.0 / fieldPx;
    vec4 s[9];
    int n = 0;
    for (int j = -1; j <= 1; ++j)
        for (int i = -1; i <= 1; ++i)
            s[n++] = texture(field, qt_TexCoord0 + vec2(float(i), float(j)) * t);
    int bi = 4;
    float bd = 1e30;
    float err = 0.0;
    for (int a = 0; a < 9; ++a) {
        float d = 0.0;
        for (int b = 0; b < 9; ++b)
            d += length(s[a].xy - s[b].xy);
        if (d < bd) { bd = d; bi = a; }
        err = max(err, s[a].z);
    }
    fragColor = vec4(s[bi].xy, mix(s[4].z, err, 0.5), mismatch(qt_TexCoord0, s[bi].xy / srcPx));
}
