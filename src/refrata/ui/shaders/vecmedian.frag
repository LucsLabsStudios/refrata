#version 440
// 3x3 vector median of the finest motion field: of the nine vectors around a block, keep the one
// closest to all the others. A lone wrong vector (a repeated texture matched one period off, a
// block on an edge) takes its neighbours' value; real motion boundaries stay sharp, unlike a blur.
// The match error (z) is the largest of the neighbourhood, so the edge of a moving object is
// treated as uncertain on both sides.
//
// w = 1 when this block found no good match at all (its own error above `badErr`). Averaged over
// the whole field (the mip chain of the next pass) that is the share of blocks that changed, the
// scene-cut test MVTools calls thscd2: a cut changes most blocks, fast motion only a few.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 fieldPx;      // size of the field, in blocks
    float badErr;      // a block's match error above this: content changed there
};
layout(binding = 1) uniform sampler2D field;

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
    fragColor = vec4(s[bi].xy, mix(s[4].z, err, 0.5), step(badErr, s[4].z));
}
