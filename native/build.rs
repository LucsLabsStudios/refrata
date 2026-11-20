// The C++ half of the module, compiled against the Qt SDK of the very version PySide6 ships
// (headers, import libraries and moc only; at run time the process already has PySide6's own Qt
// DLLs loaded, the same files by name):
//
//   vout.cpp  the video output: pictures from libVLC to Qt's video sinks without Python
//   rtx.cpp   (Windows) the RTX video item: NVIDIA RTX Video Super Resolution / HDR through D3D11
//   dxgihook.cpp (Windows) the screen's current HDR state for Qt's HDR checks (Windows HDR
//             switched on after Refrata started)
//
//   QT_DIR   the SDK's arch folder, e.g. C:\Tools\Qt\6.8.1\msvc2022_64 (aqtinstall:
//            python -m aqt install-qt windows desktop 6.8.1 win64_msvc2022_64 -m qtmultimedia)
use std::path::PathBuf;
use std::process::Command;

fn main() {
    for f in ["src/vout.cpp", "src/vout.h", "src/rtx.cpp", "src/rtx.h", "src/dxgihook.cpp"] {
        println!("cargo:rerun-if-changed={f}");
    }
    println!("cargo:rerun-if-env-changed=QT_DIR");
    let windows = cfg!(target_os = "windows");
    let qt = PathBuf::from(std::env::var("QT_DIR").unwrap_or_else(|_| {
        if windows { r"C:\Tools\Qt\6.8.1\msvc2022_64".into() } else { "/usr".into() }
    }));
    let inc = qt.join("include");
    let out = PathBuf::from(std::env::var("OUT_DIR").unwrap());
    let mut b = cc::Build::new();
    b.cpp(true)
        .file("src/vout.cpp")
        .include("src")
        .include(&inc)
        .include(inc.join("QtCore"))
        .include(inc.join("QtGui"))
        .include(inc.join("QtGui").join("6.8.1"))
        .include(inc.join("QtGui").join("6.8.1").join("QtGui"))
        .include(inc.join("QtMultimedia"))
        .include(inc.join("QtQml"))
        .include(inc.join("QtQuick"))
        .define("QT_NO_DEBUG", None)
        .define("QT_NO_KEYWORDS", None);
    if windows {
        // moc for the Q_OBJECT item
        let moc = qt.join("bin").join("moc.exe");
        let moc_out = out.join("moc_rtx.cpp");
        let st = Command::new(&moc).arg("src/rtx.h").arg("-o").arg(&moc_out).status().expect("moc");
        assert!(st.success(), "moc failed");
        b.file("src/rtx.cpp").file(&moc_out).file("src/dxgihook.cpp");
    }
    if cfg!(target_env = "msvc") {
        b.flag("/std:c++17").flag("/Zc:__cplusplus").flag("/permissive-").flag("/EHsc").flag("/utf-8");
    } else {
        b.flag("-std=c++17").flag("-fPIC");
    }
    b.compile("refrata_vout");
    let lib = qt.join("lib");
    println!("cargo:rustc-link-search=native={}", lib.display());
    for l in ["Qt6Core", "Qt6Gui", "Qt6Multimedia", "Qt6Qml", "Qt6Quick"] {
        println!("cargo:rustc-link-lib=dylib={l}");
    }
    if windows {
        println!("cargo:rustc-link-lib=dylib=d3d11");
        println!("cargo:rustc-link-lib=dylib=dxgi");
    }
    // speech recognition runs on the GPU through Vulkan (whisper.cpp). vulkan-1.dll comes with
    // every GPU driver, but loaded only when first used: on a PC without it, the module (and so the
    // frame path) still loads, only speech recognition is unavailable (aisubs.py checks first)
    if cfg!(target_env = "msvc") {
        println!("cargo:rustc-link-arg=/DELAYLOAD:vulkan-1.dll");
        println!("cargo:rustc-link-lib=delayimp");
    }
}
