//! Refrata's native helpers.
//!
//! `FramePool`: libVLC's per-picture callbacks (lock / unlock / display) in Rust. The decoder
//! thread used to enter Python three times per picture, so a busy interpreter (library scan,
//! UI work) could hold up decoding. Now:
//!
//! - Python prepares a few mapped `QVideoFrame`s and hands their plane addresses to the pool
//!   (`give`); libVLC decodes straight into them (`lock` pops one).
//! - `display` queues the frame's id and writes one byte to a socket; the UI thread wakes on it
//!   (`QSocketNotifier`), takes the ids (`take`), unmaps and shows those frames, and gives fresh
//!   ones back.
//! - With no free frame, the picture goes to a scratch buffer and is dropped (counted).
//!
//! No Python, no GIL, no allocation on the decoder thread.

use pyo3::prelude::*;
use std::collections::{HashMap, VecDeque};
use std::time::Instant;
use std::ffi::c_void;
use std::io::Write;
use std::mem::ManuallyDrop;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

const PLANES: usize = 5; // libVLC's PICTURE_PLANE_MAX

struct Slot {
    id: u64,
    planes: [usize; 3],
}

#[cfg(windows)]
type Socket = std::net::TcpStream; // Python's socketpair() on Windows is a loopback TCP pair
#[cfg(unix)]
type Socket = std::os::unix::net::UnixStream;

struct Pool {
    free: Mutex<VecDeque<Slot>>,
    ready: Mutex<VecDeque<u64>>,
    scratch: Mutex<Vec<Vec<u8>>>,
    // pictures handed to libVLC: None while it decodes into them (locked), the unlock time after.
    // libVLC drops late pictures (lock + unlock, no display): those are reclaimed after a while.
    out: Mutex<HashMap<u64, Option<Instant>>>,
    notify: ManuallyDrop<Socket>, // owned by Python: never closed here
    delivered: AtomicU64,
    dropped: AtomicU64,
}

impl Pool {
    fn wake(&self) {
        // non-blocking: if the UI has not read the previous bytes yet, it will find this frame too
        let _ = (&*self.notify).write(&[1u8]);
    }
}

// ------------------------------------------------------------------ the C callbacks
extern "C" fn lock_cb(opaque: *mut c_void, planes: *mut *mut c_void) -> *mut c_void {
    let pool = unsafe { &*(opaque as *const Pool) };
    let slot = pool.free.lock().ok().and_then(|mut f| f.pop_front());
    unsafe {
        match slot {
            Some(s) => {
                if let Ok(mut o) = pool.out.lock() {
                    o.insert(s.id, None);
                }
                for (i, p) in s.planes.iter().enumerate() {
                    *planes.add(i) = *p as *mut c_void;
                }
                s.id as *mut c_void
            }
            None => {
                // no frame free: decode into the scratch buffers, the picture is dropped
                let mut sc = pool.scratch.lock().unwrap();
                for i in 0..PLANES {
                    let p = sc.get_mut(i).map(|v| v.as_mut_ptr()).unwrap_or(std::ptr::null_mut());
                    *planes.add(i) = p as *mut c_void;
                }
                std::ptr::null_mut()
            }
        }
    }
}

extern "C" fn unlock_cb(opaque: *mut c_void, picture: *mut c_void, _planes: *const *mut c_void) {
    let pool = unsafe { &*(opaque as *const Pool) };
    let id = picture as u64;
    if id != 0 {
        if let Ok(mut o) = pool.out.lock() {
            if let Some(v) = o.get_mut(&id) {
                *v = Some(Instant::now()); // libVLC is done writing: the memory is ours again
            }
        }
    }
}

extern "C" fn display_cb(opaque: *mut c_void, picture: *mut c_void) {
    let pool = unsafe { &*(opaque as *const Pool) };
    let id = picture as u64;
    if id == 0 {
        pool.dropped.fetch_add(1, Ordering::Relaxed);
        return;
    }
    if let Ok(mut o) = pool.out.lock() {
        if o.remove(&id).is_none() {
            return; // reclaimed already (shown very late): not ours to deliver any more
        }
    }
    if let Ok(mut r) = pool.ready.lock() {
        r.push_back(id);
    }
    pool.delivered.fetch_add(1, Ordering::Relaxed);
    pool.wake();
}

// ------------------------------------------------------------------ Python API
#[pyclass]
struct FramePool {
    inner: Arc<Pool>,
}

#[pymethods]
impl FramePool {
    /// `notify`: the write end of a socket pair (its fileno()); the UI thread watches the other end.
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
        Ok(FramePool {
            inner: Arc::new(Pool {
                free: Mutex::new(VecDeque::new()),
                ready: Mutex::new(VecDeque::new()),
                scratch: Mutex::new(Vec::new()),
                out: Mutex::new(HashMap::new()),
                notify: ManuallyDrop::new(sock),
                delivered: AtomicU64::new(0),
                dropped: AtomicU64::new(0),
            }),
        })
    }

    /// (lock, unlock, display, opaque) addresses for libvlc_video_set_callbacks. The pool lives as
    /// long as this object; keep it alive while the player can call back.
    fn callbacks(&self) -> (usize, usize, usize, usize) {
        (
            lock_cb as *const () as usize,
            unlock_cb as *const () as usize,
            display_cb as *const () as usize,
            Arc::as_ptr(&self.inner) as usize,
        )
    }

    /// A new picture layout: scratch buffers of these plane sizes; frames given before are forgotten
    /// (their ids are returned so Python can drop them).
    fn configure(&self, plane_sizes: Vec<usize>) -> Vec<u64> {
        *self.inner.scratch.lock().unwrap() = plane_sizes.iter().map(|n| vec![0u8; *n]).collect();
        let mut old: Vec<u64> = self.inner.free.lock().unwrap().drain(..).map(|s| s.id).collect();
        old.extend(self.inner.ready.lock().unwrap().drain(..));
        // pictures libVLC finished with (unlocked) can go; ones it still writes stay until it's done
        let mut out = self.inner.out.lock().unwrap();
        let done: Vec<u64> = out.iter().filter(|(_, t)| t.is_some()).map(|(id, _)| *id).collect();
        for id in &done {
            out.remove(id);
        }
        old.extend(done);
        old
    }

    /// A mapped frame libVLC may decode into: its id (> 0) and plane addresses.
    fn give(&self, id: u64, planes: Vec<usize>) -> PyResult<()> {
        if id == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err("frame id must be > 0"));
        }
        let mut p = [0usize; 3];
        for (i, a) in planes.iter().take(3).enumerate() {
            p[i] = *a;
        }
        self.inner.free.lock().unwrap().push_back(Slot { id, planes: p });
        Ok(())
    }

    /// Ids of the frames libVLC has finished, oldest first.
    fn take(&self) -> Vec<u64> {
        self.inner.ready.lock().unwrap().drain(..).collect()
    }

    /// (delivered, dropped) pictures since the pool was made.
    fn stats(&self) -> (u64, u64) {
        (
            self.inner.delivered.load(Ordering::Relaxed),
            self.inner.dropped.load(Ordering::Relaxed),
        )
    }

    /// Pictures libVLC unlocked more than `ms` ago and never displayed (dropped as late): their
    /// frames can be freed or reused. A picture still being written is never returned.
    fn reclaim(&self, ms: u64) -> Vec<u64> {
        let mut out = self.inner.out.lock().unwrap();
        let old: Vec<u64> = out
            .iter()
            .filter(|(_, t)| t.map(|t| t.elapsed().as_millis() as u64 >= ms).unwrap_or(false))
            .map(|(id, _)| *id)
            .collect();
        for id in &old {
            out.remove(id);
        }
        old
    }

    /// Pictures libVLC holds right now (locked or waiting for display).
    fn outstanding(&self) -> usize {
        self.inner.out.lock().unwrap().len()
    }

    fn free_count(&self) -> usize {
        self.inner.free.lock().unwrap().len()
    }
}

#[pymodule]
fn refrata_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<FramePool>()?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
