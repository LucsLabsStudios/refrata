import QtQuick
import Refrata.Native

// The picture through the GPU's video processor with NVIDIA RTX Video (native/src/rtx.cpp).
// Loaded by VideoPipeline only when the native module registered the type.
RtxVideo {
    objectName: "rtxVideo"
}
