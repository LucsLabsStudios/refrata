//! Refrata's native helpers.
//!
//! `VideoOut`: the whole per-picture path from libVLC to the screen, with no Python in it.
//!
//! - libVLC's lock / unlock / display callbacks are the Rust functions below, on its decoder
//!   thread. `lock` hands out memory from a pool (64-byte aligned planes, reused: no 12 MB
//!   allocation per 4K picture); `display` passes the picture to vout.cpp, which wraps that very
//!   memory in a QVideoFrame and gets it to the QVideoSinks on the UI thread, in C++.
//! - When Qt is done with a picture (shown, uploaded, replaced) its buffer comes back to the pool
//!   (`rv_buffer_release`).
//! - Python is told only about the first picture of each item (one byte on a socket); it sets the
//!   format once per file and the sinks when the screen changes.
//!
//! The decoder thread never takes the GIL, so a busy interpreter (library scan, yt-dlp, HDR10+
//! reading) cannot hold up a single frame.

use pyo3::prelude::*;
use std::collections::HashMap;
use std::ffi::c_void;
use std::io::Write;
use std::mem::ManuallyDrop;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::Instant;

mod asr;
mod hdr10plus;

const PLANES: usize = 5; // libVLC's PICTURE_PLANE_MAX
const ALIGN: usize = 64;
const KEEP_FREE: usize = 8; // spare buffers kept for reuse
const RECLAIM_MS: u128 = 3000; // unlocked, never displayed for this long: libVLC dropped it

extern "C" {
    fn rv_router_new() -> *mut c_void;
    fn rv_router_free(r: *mut c_void);
    fn rv_set_format(r: *mut c_void, w: i32, h: i32, pixfmt: i32, cs: i32, full: i32, vw: i32, vh: i32);
    fn rv_deliver(r: *mut c_void, pool: *mut c_void, handle: u64, planes: i32, data: *mut *mut u8,
                  pitch: *const i32, size: *const i32) -> i32;
    fn rv_set_sinks(r: *mut c_void, sinks: *mut *mut c_void, n: i32);
    fn rv_set_pair(r: *mut c_void, a: *mut c_void, b: *mut c_void, pipeline: *mut c_void);
    fn rv_clear(r: *mut c_void);
    fn rv_last_frame(r: *mut c_void) -> *mut c_void;
    fn rv_frame_free(f: *mut c_void);
    fn rv_delivered(r: *mut c_void) -> u64;
    fn rv_skipped(r: *mut c_void) -> u64;
    fn rv_recent_slots(r: *mut c_void, out: *mut i32, n: i32) -> i32;
    fn rv_recent_times(r: *mut c_void, out: *mut i64, n: i32) -> i32;
    #[cfg(windows)]
    fn rv_register_qml();
}

#[cfg(windows)]
type Socket = std::net::TcpStream; // Python's socketpair() on Windows is a loopback TCP pair
#[cfg(unix)]
type Socket = std::os::unix::net::UnixStream;

struct Buf {
    mem: Vec<u8>,
    gen: u64,
}

impl Buf {
    fn base(&mut self) -> *mut u8 {
        let p = self.mem.as_mut_ptr();
        let off = (ALIGN - (p as usize) % ALIGN) % ALIGN;
        unsafe { p.add(off) }
    }
}

#[derive(Clone, Default)]
struct Layout {
    gen: u64,
    planes: usize,
    pitch: [i32; 4],
    size: [i32; 4],   // bytes of each plane
    offset: [usize; 4],
    total: usize,
}

struct Pool {
    layout: Mutex<Layout>,
    free: Mutex<Vec<Buf>>,
    // pictures libVLC holds: None while it decodes, the unlock time after
    out: Mutex<HashMap<u64, (Buf, Option<Instant>)>>,
    lent: Mutex<HashMap<u64, Buf>>, // pictures Qt holds
    next: AtomicU64,
    router: RwLock<usize>,          // vout.cpp's Router; 0 once closed
    notify: ManuallyDrop<Socket>,   // owned by Python: never closed here
    player_gen: AtomicU64,          // the item libVLC is playing
    want_gen: AtomicU64,            // the item the app shows: pictures of others are not shown
    first_pending: AtomicBool,      // tell Python about the next picture shown
    decoded: AtomicU64,
    stale: AtomicU64,
}

impl Pool {
    fn take(&self) -> Option<(u64, Buf, Layout)> {
        let lay = self.layout.lock().ok()?.clone();
        if lay.total == 0 {
            return None;
        }
        let reuse = {
            let mut f = self.free.lock().ok()?;
            let mut got = None;
            while let Some(b) = f.pop() {
                if b.gen == lay.gen {
                    got = Some(b);
                    break;
                }
            }
            got
        };
        let buf = reuse.unwrap_or_else(|| Buf { mem: vec![0u8; lay.total + ALIGN], gen: lay.gen });
        Some((self.next.fetch_add(1, Ordering::Relaxed), buf, lay))
    }

    fn give_back(&self, buf: Buf) {
        let cur = self.layout.lock().map(|l| l.gen).unwrap_or(0);
        if buf.gen == cur {
            if let Ok(mut f) = self.free.lock() {
                if f.len() < KEEP_FREE {
                    f.push(buf);
                }
            }
        }
    }

    fn reclaim(&self) {
        let old: Vec<u64> = match self.out.lock() {
            Ok(o) if o.len() > 12 => o
                .iter()
                .filter(|(_, (_, t))| t.map(|t| t.elapsed().as_millis() >= RECLAIM_MS).unwrap_or(false))
                .map(|(id, _)| *id)
                .collect(),
            _ => return,
        };
        for id in old {
            let b = self.out.lock().ok().and_then(|mut o| o.remove(&id));
            if let Some((b, _)) = b {
                self.give_back(b);
            }
        }
    }
}

// ------------------------------------------------------------------ libVLC callbacks
extern "C" fn lock_cb(opaque: *mut c_void, planes: *mut *mut c_void) -> *mut c_void {
    let pool = unsafe { &*(opaque as *const Pool) };
    pool.reclaim();
    match pool.take() {
        Some((id, mut buf, lay)) => {
            let base = buf.base();
            unsafe {
                for i in 0..PLANES {
                    let p = if i < lay.planes { base.add(lay.offset[i]) } else { std::ptr::null_mut() };
                    *planes.add(i) = p as *mut c_void;
                }
            }
            if let Ok(mut o) = pool.out.lock() {
                o.insert(id, (buf, None));
            }
            id as *mut c_void
        }
        None => {
            // no layout yet (should not happen after setup): a buffer nobody shows
            unsafe {
                for i in 0..PLANES {
                    *planes.add(i) = std::ptr::null_mut();
                }
            }
            std::ptr::null_mut()
        }
    }
}

extern "C" fn unlock_cb(opaque: *mut c_void, picture: *mut c_void, _planes: *const *mut c_void) {
    let pool = unsafe { &*(opaque as *const Pool) };
    if let Ok(mut o) = pool.out.lock() {
        if let Some(v) = o.get_mut(&(picture as u64)) {
            v.1 = Some(Instant::now());
        }
    }
}

extern "C" fn display_cb(opaque: *mut c_void, picture: *mut c_void) {
    let pool_ptr = opaque as *const Pool;
    let pool = unsafe { &*pool_ptr };
    let id = picture as u64;
    let Some((mut buf, _)) = pool.out.lock().ok().and_then(|mut o| o.remove(&id)) else {
        return; // reclaimed already, or shown before: not ours to show again
    };
    pool.decoded.fetch_add(1, Ordering::Relaxed);
    let lay = pool.layout.lock().map(|l| l.clone()).unwrap_or_default();
    if buf.gen != lay.gen || pool.player_gen.load(Ordering::Acquire) != pool.want_gen.load(Ordering::Acquire) {
        // a picture of the previous layout or of the item before: never on screen
        pool.stale.fetch_add(1, Ordering::Relaxed);
        pool.give_back(buf);
        return;
    }
    let router = pool.router.read().unwrap();
    if *router == 0 {
        pool.give_back(buf);
        return;
    }
    let base = buf.base();
    let mut data = [std::ptr::null_mut::<u8>(); 4];
    for i in 0..lay.planes {
        data[i] = unsafe { base.add(lay.offset[i]) };
    }
    pool.lent.lock().unwrap().insert(id, buf);
    // the frame keeps the pool alive until Qt releases it (rv_buffer_release drops this reference)
    let keep = unsafe {
        Arc::increment_strong_count(pool_ptr);
        pool_ptr as *mut c_void
    };
    let ok = unsafe {
        rv_deliver(*router as *mut c_void, keep, id, lay.planes as i32, data.as_mut_ptr(),
                   lay.pitch.as_ptr(), lay.size.as_ptr())
    };
    drop(router);
    if ok != 0 && pool.first_pending.swap(false, Ordering::AcqRel) {
        let _ = (&*pool.notify).write(&[1u8]);
    }
}

/// Called by vout.cpp when Qt lets go of a picture (any thread).
#[no_mangle]
pub extern "C" fn rv_buffer_release(pool: *mut c_void, handle: u64) {
    let p = pool as *const Pool;
    unsafe {
        let pool = &*p;
        if let Some(b) = pool.lent.lock().ok().and_then(|mut l| l.remove(&handle)) {
            pool.give_back(b);
        }
        Arc::decrement_strong_count(p);
    }
}

// ------------------------------------------------------------------ Python API
#[pyclass]
struct VideoOut {
    pool: Arc<Pool>,
}

impl VideoOut {
    /// the router, or null once closed (vout.cpp functions take null as "nothing to do")
    fn r(&self) -> *mut c_void {
        *self.pool.router.read().unwrap() as *mut c_void
    }
}

#[pymethods]
impl VideoOut {
    /// `notify`: the write end of a socket pair (its fileno()). Make it on the UI thread: the
    /// router lives there.
    #[new]
    fn new(notify: i64) -> PyResult<Self> {
        #[cfg(windows)]
        let sock = unsafe {
            use std::os::windows::io::FromRawSocket;
            std::net::TcpStream::from_raw_socket(notify as u64)
        };
        #[cfg(unix)]
        let sock = unsafe {
            use std::os::unix::io::FromRawFd;
            std::os::unix::net::UnixStream::from_raw_fd(notify as i32)
        };
        sock.set_nonblocking(true)?;
        let router = unsafe { rv_router_new() } as usize;
        Ok(VideoOut {
            pool: Arc::new(Pool {
                layout: Mutex::new(Layout::default()),
                free: Mutex::new(Vec::new()),
                out: Mutex::new(HashMap::new()),
                lent: Mutex::new(HashMap::new()),
                next: AtomicU64::new(1),
                router: RwLock::new(router),
                notify: ManuallyDrop::new(sock),
                player_gen: AtomicU64::new(0),
                want_gen: AtomicU64::new(0),
                first_pending: AtomicBool::new(true),
                decoded: AtomicU64::new(0),
                stale: AtomicU64::new(0),
            }),
        })
    }

    /// (lock, unlock, display, opaque) for libvlc_video_set_callbacks. The opaque pointer stays
    /// valid for the life of the process (libVLC may call back until its player is released).
    fn callbacks(&self) -> (usize, usize, usize, usize) {
        let opaque = Arc::into_raw(self.pool.clone()) as usize; // one reference libVLC keeps
        (lock_cb as *const () as usize, unlock_cb as *const () as usize, display_cb as *const () as usize, opaque)
    }

    /// The picture layout libVLC will write (format callback): plane pitches and line counts, and
    /// what Qt calls it (pixfmt 0 NV12, 1 YUV420P, 2 P010; cs 0 601, 1 709, 2 2020).
    #[allow(clippy::too_many_arguments)]
    fn configure(&self, pitches: Vec<i32>, lines: Vec<i32>, w: i32, h: i32, pixfmt: i32, cs: i32, full: bool) {
        let mut lay = Layout::default();
        let mut off = 0usize;
        lay.planes = pitches.len().min(lines.len()).min(4);
        for i in 0..lay.planes {
            lay.pitch[i] = pitches[i];
            lay.size[i] = pitches[i] * lines[i];
            lay.offset[i] = off;
            off += ((lay.size[i] as usize) + ALIGN - 1) / ALIGN * ALIGN;
        }
        lay.total = off;
        {
            let mut l = self.pool.layout.lock().unwrap();
            lay.gen = l.gen + 1;
            *l = lay;
        }
        self.pool.free.lock().unwrap().clear();
        unsafe { rv_set_format(self.r(), w, h, pixfmt, cs, full as i32, 0, 0) };
    }

    /// Show only the top-left vw×vh (decoders pad pictures to their block size).
    #[allow(clippy::too_many_arguments)]
    fn set_viewport(&self, w: i32, h: i32, pixfmt: i32, cs: i32, full: bool, vw: i32, vh: i32) {
        unsafe { rv_set_format(self.r(), w, h, pixfmt, cs, full as i32, vw, vh) };
    }

    /// Which item libVLC plays and which one the app shows; pictures show only when they match.
    fn set_gens(&self, player: u64, want: u64) {
        self.pool.player_gen.store(player, Ordering::Release);
        self.pool.want_gen.store(want, Ordering::Release);
    }

    /// A new item: forget the picture on screen, and say when its first picture shows.
    fn reset(&self) {
        self.pool.first_pending.store(true, Ordering::Release);
        unsafe { rv_clear(self.r()) };
    }

    /// QVideoSink addresses (shiboken6.getCppPointer) every picture goes to.
    fn set_sinks(&self, sinks: Vec<usize>) {
        let mut v: Vec<*mut c_void> = sinks.iter().map(|s| *s as *mut c_void).collect();
        unsafe { rv_set_sinks(self.r(), v.as_mut_ptr(), v.len() as i32) };
    }

    /// Smooth motion: pictures go to a and b in turn and pipeline.frameArrived(slot) is called;
    /// zeros: back to the plain sinks.
    fn set_pair(&self, a: usize, b: usize, pipeline: usize) {
        unsafe { rv_set_pair(self.r(), a as *mut c_void, b as *mut c_void, pipeline as *mut c_void) };
    }

    /// A new QVideoFrame* copy of the picture on screen (wrap with shiboken6, then free_frame), or 0.
    fn last_frame(&self) -> usize {
        unsafe { rv_last_frame(self.r()) as usize }
    }

    fn free_frame(&self, f: usize) {
        if f != 0 {
            unsafe { rv_frame_free(f as *mut c_void) };
        }
    }

    /// Pictures on screen so far (UI thread count).
    fn shown(&self) -> u64 {
        unsafe { rv_delivered(self.r()) }
    }

    /// (decoded, shown, skipped for a newer one, stale) since creation.
    fn stats(&self) -> (u64, u64, u64, u64) {
        unsafe {
            (self.pool.decoded.load(Ordering::Relaxed), rv_delivered(self.r()),
             rv_skipped(self.r()), self.pool.stale.load(Ordering::Relaxed))
        }
    }

    /// The last pair slots used (tests: they alternate).
    fn recent_slots(&self) -> Vec<i32> {
        let mut v = [0i32; 32];
        let n = unsafe { rv_recent_slots(self.r(), v.as_mut_ptr(), 32) };
        v[..n.max(0) as usize].to_vec()
    }

    /// The router's address, for the RTX video item (QML RtxVideo.router); 0 once closed.
    fn router(&self) -> usize {
        self.r() as usize
    }

    /// When the last pictures (up to 512) reached the sinks, in microseconds (frame pacing checks).
    fn recent_times(&self) -> Vec<i64> {
        let mut v = vec![0i64; 512];
        let n = unsafe { rv_recent_times(self.r(), v.as_mut_ptr(), 512) };
        v.truncate(n.max(0) as usize);
        v
    }

    /// Pictures libVLC or Qt hold right now.
    fn outstanding(&self) -> (usize, usize, usize) {
        (self.pool.out.lock().unwrap().len(), self.pool.lent.lock().unwrap().len(), self.pool.free.lock().unwrap().len())
    }

    /// Stop showing (the player is going away); waits for a delivery in progress.
    fn close(&mut self) {
        let mut r = self.pool.router.write().unwrap();
        if *r != 0 {
            unsafe { rv_router_free(*r as *mut c_void) };
            *r = 0;
        }
        self.pool.free.lock().unwrap().clear();
    }
}

impl Drop for VideoOut {
    fn drop(&mut self) {
        self.close();
    }
}

/// Register the QML types (RtxVideo, "import Refrata.Native"): after the QGuiApplication, before QML.
#[pyfunction]
fn register_qml() -> bool {
    #[cfg(windows)]
    {
        unsafe { rv_register_qml() };
        true
    }
    #[cfg(not(windows))]
    false
}

#[cfg(windows)]
extern "C" {
    fn rv_dxgi_hook_install() -> i32;
    fn rv_dxgi_override(name: *const u16, hdr: i32);
    fn rv_dxgi_hits() -> std::os::raw::c_long;
}

/// (Windows) Make DXGI's answer to Qt's "is this screen in HDR?" the current one (dxgihook.cpp).
#[pyfunction]
fn dxgi_hook_install() -> bool {
    #[cfg(windows)]
    {
        unsafe { rv_dxgi_hook_install() == 1 }
    }
    #[cfg(not(windows))]
    false
}

/// (Windows) What the display `name` (\\.\DISPLAY1) is now: 1 HDR, 0 SDR, -1 let DXGI answer.
#[pyfunction]
fn dxgi_override(name: &str, hdr: i32) {
    #[cfg(windows)]
    {
        let w: Vec<u16> = name.encode_utf16().chain(std::iter::once(0)).collect();
        unsafe { rv_dxgi_override(w.as_ptr(), hdr) };
    }
    #[cfg(not(windows))]
    let _ = (name, hdr);
}

/// (Windows) How many DXGI answers were corrected (tests).
#[pyfunction]
fn dxgi_hits() -> i64 {
    #[cfg(windows)]
    {
        unsafe { rv_dxgi_hits() as i64 }
    }
    #[cfg(not(windows))]
    0
}

#[pymodule]
fn refrata_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(register_qml, m)?)?;
    m.add_function(wrap_pyfunction!(dxgi_hook_install, m)?)?;
    m.add_function(wrap_pyfunction!(dxgi_override, m)?)?;
    m.add_function(wrap_pyfunction!(dxgi_hits, m)?)?;
    m.add_class::<VideoOut>()?;
    m.add_class::<hdr10plus::Hdr10PlusReader>()?;
    m.add_class::<asr::Transcriber>()?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
