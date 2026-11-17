#version 440
// The whole main window on an HDR screen (Main.qml, hdroutput.py): everything renders, as usual,
// into one 16-bit layer in sRGB-encoded values, composited over the window colour; this turns it
// into scRGB light for the swapchain (linear, 1.0 = 80 nits). `scale` = the desktop's SDR white /
// 80, so the interface is exactly as bright as every other window. The video's HDR highlights
// arrive above 1.0 (present.frag writes them in the same extended sRGB curve) and come out above
// SDR white; colours outside BT.709 arrive negative and stay negative, as scRGB wants them.
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float scale;
    vec4 bg;           // the window colour (opaque, sRGB)
};
layout(binding = 1) uniform sampler2D source;

vec3 srgbDecode(vec3 c) {
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(vec3(0.04045), c));
}

void main() {
    vec4 c = texture(source, qt_TexCoord0);
    vec3 rgb = c.rgb + bg.rgb * (1.0 - c.a);
    fragColor = vec4(sign(rgb) * srgbDecode(abs(rgb)) * scale, 1.0);
}
