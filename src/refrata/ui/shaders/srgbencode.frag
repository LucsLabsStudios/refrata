#version 440
// In a scRGB window Qt's video shaders write linear light (their *_linear variants), while every
// pass after them, and the window's HDR layer (hdrlayer.frag), expects the video's own encoded
// values: decoded twice, 128 came out as 41. This puts the sRGB curve back, exactly undoing Qt's
// step; PQ and HLG frames (handed to Qt without their transfer) get their code values back too.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
};
layout(binding = 1) uniform sampler2D source;

void main() {
    vec4 c = texture(source, qt_TexCoord0);
    vec3 a = abs(c.rgb);
    vec3 e = mix(a * 12.92, 1.055 * pow(a, vec3(1.0 / 2.4)) - 0.055, step(vec3(0.0031308), a));
    fragColor = vec4(sign(c.rgb) * e, c.a) * qt_Opacity;
}
