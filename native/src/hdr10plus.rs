//! HDR10+ (SMPTE ST 2094-40) scene metadata, read from MP4 and Matroska files on a Rust thread.
//! The same reading as refrata/hdr10plus.py (the fallback), without the GIL: a 2-hour film is
//! ~170 000 samples to look at, and in Python that work held up the frames on screen.

use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

const PROBE_SAMPLES: usize = 48;
const SAMPLE_HEAD: usize = 1536;
const MAX_SAMPLES: usize = 2_000_000;
const MAX_BOXES: usize = 4000;

#[derive(Clone, PartialEq, Debug)]
pub struct Meta {
    peak: f64,
    p99: f64,
    avg: f64,
    target: f64,
    knee: Option<(f64, f64)>,
    anchors: Vec<f64>,
}

// ------------------------------------------------------------------ bits
struct Bits<'a> {
    d: &'a [u8],
    p: usize,
}

impl<'a> Bits<'a> {
    fn u(&mut self, k: usize) -> Option<u64> {
        if self.p + k > self.d.len() * 8 {
            return None;
        }
        let mut v = 0u64;
        for _ in 0..k {
            v = (v << 1) | ((self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1) as u64;
            self.p += 1;
        }
        Some(v)
    }
    fn skip(&mut self, k: usize) -> Option<()> {
        if self.p + k > self.d.len() * 8 {
            return None;
        }
        self.p += k;
        Some(())
    }
}

fn unescape(nal: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(nal.len());
    let mut zeros = 0;
    for &b in nal {
        if zeros >= 2 && b == 3 {
            zeros = 0;
            continue;
        }
        out.push(b);
        zeros = if b == 0 { zeros + 1 } else { 0 };
    }
    out
}

pub fn parse_t35(p: &[u8]) -> Option<Meta> {
    if p.len() < 7 || p[0] != 0xB5 || p[1..3] != [0x00, 0x3C] || p[3..5] != [0x00, 0x01] || p[5] != 4 || p[6] > 1 {
        return None;
    }
    let mut r = Bits { d: &p[7..], p: 0 };
    let windows = r.u(2)? as usize;
    if windows < 1 {
        return None;
    }
    for _ in 0..windows - 1 {
        r.skip(16 * 6 + 8 + 16 * 3 + 1)?;
    }
    let target = r.u(27)?;
    if r.u(1)? == 1 {
        let (rows, cols) = (r.u(5)? as usize, r.u(5)? as usize);
        r.skip(4 * rows * cols)?;
    }
    let (mut maxscl, mut avg, mut pct) = ([0u64; 3], 0u64, Vec::new());
    for w in 0..windows {
        let m = [r.u(17)?, r.u(17)?, r.u(17)?];
        let a = r.u(17)?;
        let n = r.u(4)?;
        let mut ps = Vec::new();
        for _ in 0..n {
            ps.push((r.u(7)?, r.u(17)?));
        }
        r.u(10)?;
        if w == 0 {
            maxscl = m;
            avg = a;
            pct = ps;
        }
    }
    if r.u(1)? == 1 {
        let (rows, cols) = (r.u(5)? as usize, r.u(5)? as usize);
        r.skip(4 * rows * cols)?;
    }
    let (mut knee, mut anchors) = (None, Vec::new());
    for w in 0..windows {
        if r.u(1)? == 1 {
            let (kx, ky, n) = (r.u(12)?, r.u(12)?, r.u(4)?);
            let mut an = Vec::new();
            for _ in 0..n {
                an.push(r.u(10)? as f64 / 1023.0);
            }
            if w == 0 {
                knee = Some((kx as f64 / 4095.0, ky as f64 / 4095.0));
                anchors = an;
            }
        }
        if r.u(1)? == 1 {
            r.u(6)?;
        }
    }
    let peak = *maxscl.iter().max().unwrap() as f64 / 10.0;
    let top = pct.iter().filter(|x| x.0 <= 100).max_by_key(|x| x.0).copied();
    let p99 = match top {
        Some((pc, v)) if pc >= 98 => v as f64 / 10.0,
        _ => peak,
    };
    Some(Meta { peak, p99, avg: avg as f64 / 10.0, target: target as f64, knee, anchors })
}

fn sei_t35(nal: &[u8]) -> Vec<Vec<u8>> {
    let d = unescape(nal);
    let d = &d[2.min(d.len())..];
    let mut out = Vec::new();
    let mut i = 0;
    while i + 2 < d.len() {
        let mut t = 0usize;
        while i < d.len() && d[i] == 0xFF {
            t += 255;
            i += 1;
        }
        if i >= d.len() {
            break;
        }
        t += d[i] as usize;
        i += 1;
        let mut size = 0usize;
        while i < d.len() && d[i] == 0xFF {
            size += 255;
            i += 1;
        }
        if i >= d.len() {
            break;
        }
        size += d[i] as usize;
        i += 1;
        if t == 4 {
            out.push(d[i..(i + size).min(d.len())].to_vec());
        }
        i += size;
        if i < d.len() && d[i] == 0x80 {
            break;
        }
    }
    out
}

fn from_sample(head: &[u8], nal_len: usize) -> Option<Meta> {
    let mut i = 0;
    while i + nal_len < head.len() {
        let mut n = 0usize;
        for k in 0..nal_len {
            n = (n << 8) | head[i + k] as usize;
        }
        i += nal_len;
        if n < 2 {
            return None;
        }
        let kind = (head[i] >> 1) & 0x3F;
        if kind < 32 {
            return None;
        }
        if kind == 39 {
            let end = (i + n).min(head.len());
            for p in sei_t35(&head[i..end]) {
                if let Some(m) = parse_t35(&p) {
                    return Some(m);
                }
            }
        }
        i += n;
    }
    None
}

// ------------------------------------------------------------------ timeline
#[derive(Default)]
struct Timeline {
    times: Vec<f64>,
    items: Vec<Meta>,
}

impl Timeline {
    fn add(&mut self, ms: f64, m: Meta) {
        let k = self.times.partition_point(|&t| t <= ms);
        if k > 0 && self.items[k - 1] == m {
            return;
        }
        if k < self.items.len() && self.items[k] == m {
            self.times[k] = ms;
            return;
        }
        self.times.insert(k, ms);
        self.items.insert(k, m);
    }
    fn index_at(&self, ms: f64) -> Option<usize> {
        if self.items.is_empty() {
            return None;
        }
        let k = self.times.partition_point(|&t| t <= ms);
        Some(k.saturating_sub(1))
    }
}

struct Shared {
    tl: Mutex<Timeline>,
    stop: AtomicBool,
    done: AtomicBool,
}

impl Shared {
    fn any(&self) -> bool {
        !self.tl.lock().unwrap().items.is_empty()
    }
    fn add(&self, ms: f64, m: Meta) {
        self.tl.lock().unwrap().add(ms, m);
    }
}

// ------------------------------------------------------------------ reading
fn read_at(f: &mut File, pos: u64, n: usize) -> Option<Vec<u8>> {
    f.seek(SeekFrom::Start(pos)).ok()?;
    let mut v = vec![0u8; n];
    let mut got = 0;
    while got < n {
        match f.read(&mut v[got..]) {
            Ok(0) => break,
            Ok(k) => got += k,
            Err(_) => return None,
        }
    }
    v.truncate(got);
    Some(v)
}

fn be(b: &[u8]) -> u64 {
    b.iter().fold(0u64, |v, x| (v << 8) | *x as u64)
}

/// (kind, body start, end) of the boxes in [start, end)
fn boxes(f: &mut File, start: u64, end: u64, budget: &mut usize) -> Vec<([u8; 4], u64, u64)> {
    let mut out = Vec::new();
    let mut pos = start;
    while pos + 8 <= end && *budget > 0 {
        *budget -= 1;
        let Some(h) = read_at(f, pos, 16) else { break };
        if h.len() < 8 {
            break;
        }
        let mut size = be(&h[0..4]);
        let kind = [h[4], h[5], h[6], h[7]];
        let mut hdr = 8;
        if size == 1 {
            if h.len() < 16 {
                break;
            }
            size = be(&h[8..16]);
            hdr = 16;
        } else if size == 0 {
            size = end - pos;
        }
        if size < hdr || pos + size > end + 8 {
            break;
        }
        out.push((kind, pos + hdr, (pos + size).min(end)));
        pos += size;
    }
    out
}

fn mp4_samples(f: &mut File, size: u64) -> Vec<(f64, u64, u32, usize)> {
    let mut budget = MAX_BOXES;
    for (k, a, b) in boxes(f, 0, size, &mut budget) {
        if &k != b"moov" {
            continue;
        }
        for (tk, ta, tb) in boxes(f, a, b, &mut budget) {
            if &tk != b"trak" {
                continue;
            }
            let (mut video, mut timescale, mut nal_len) = (false, 0u64, 0usize);
            let mut tables: std::collections::HashMap<[u8; 4], Vec<u8>> = Default::default();
            for (mk, ma, mb) in boxes(f, ta, tb, &mut budget) {
                if &mk != b"mdia" {
                    continue;
                }
                for (k2, a2, b2) in boxes(f, ma, mb, &mut budget) {
                    match &k2 {
                        b"mdhd" => {
                            if let Some(h) = read_at(f, a2, 32) {
                                if h.len() >= 24 {
                                    timescale = if h[0] == 1 { be(&h[20..24]) } else { be(&h[12..16]) };
                                }
                            }
                        }
                        b"hdlr" => video |= read_at(f, a2 + 8, 4).as_deref() == Some(b"vide"),
                        b"minf" => {
                            for (k3, a3, b3) in boxes(f, a2, b2, &mut budget) {
                                if &k3 != b"stbl" {
                                    continue;
                                }
                                for (k4, a4, b4) in boxes(f, a3, b3, &mut budget) {
                                    if &k4 == b"stsd" {
                                        for (ek, ea, eb) in boxes(f, a4 + 8, b4, &mut budget) {
                                            if !matches!(&ek, b"hvc1" | b"hev1" | b"dvh1" | b"dvhe") {
                                                continue;
                                            }
                                            for (ck, ca, _cb) in boxes(f, ea + 78, eb, &mut budget) {
                                                if &ck == b"hvcC" {
                                                    if let Some(body) = read_at(f, ca, 64) {
                                                        if body.len() > 21 {
                                                            nal_len = (body[21] & 3) as usize + 1;
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    } else if matches!(&k4, b"stsz" | b"stco" | b"co64" | b"stsc" | b"stts" | b"ctts") {
                                        let n = ((b4 - a4) as usize).min(64 << 20);
                                        if let Some(v) = read_at(f, a4, n) {
                                            tables.insert(k4, v);
                                        }
                                    }
                                }
                            }
                        }
                        _ => {}
                    }
                }
            }
            if !video {
                continue;
            }
            if nal_len == 0 || timescale == 0 || !tables.contains_key(b"stsz") {
                return Vec::new();
            }
            return mp4_table(&tables, timescale, nal_len).unwrap_or_default();
        }
    }
    Vec::new()
}

fn mp4_table(t: &std::collections::HashMap<[u8; 4], Vec<u8>>, timescale: u64, nal_len: usize) -> Option<Vec<(f64, u64, u32, usize)>> {
    let sz = t.get(b"stsz")?;
    let fixed = be(sz.get(4..8)?) as u32;
    let count = (be(sz.get(8..12)?) as usize).min(MAX_SAMPLES);
    let sizes: Vec<u32> = if fixed != 0 {
        vec![fixed; count]
    } else {
        (0..count).map(|k| sz.get(12 + 4 * k..16 + 4 * k).map(|b| be(b) as u32)).collect::<Option<_>>()?
    };
    let chunks: Vec<u64> = if let Some(c) = t.get(b"co64") {
        let n = be(c.get(4..8)?) as usize;
        (0..n).map(|k| c.get(8 + 8 * k..16 + 8 * k).map(be)).collect::<Option<_>>()?
    } else {
        let c = t.get(b"stco")?;
        let n = be(c.get(4..8)?) as usize;
        (0..n).map(|k| c.get(8 + 4 * k..12 + 4 * k).map(be)).collect::<Option<_>>()?
    };
    let sc = t.get(b"stsc")?;
    let ns = be(sc.get(4..8)?) as usize;
    let runs: Vec<(u64, u64)> = (0..ns)
        .map(|k| Some((be(sc.get(8 + 12 * k..12 + 12 * k)?), be(sc.get(12 + 12 * k..16 + 12 * k)?))))
        .collect::<Option<_>>()?;
    let mut offsets = Vec::with_capacity(count);
    for (r, &(first, per)) in runs.iter().enumerate() {
        let last = if r + 1 < runs.len() { runs[r + 1].0 as usize - 1 } else { chunks.len() };
        for c in (first as usize).saturating_sub(1)..last.min(chunks.len()) {
            let mut pos = chunks[c];
            for _ in 0..per {
                let k = offsets.len();
                if k >= count {
                    break;
                }
                offsets.push(pos);
                pos += sizes[k] as u64;
            }
        }
    }
    let mut times: Vec<i64> = Vec::with_capacity(count);
    if let Some(st) = t.get(b"stts") {
        let mut dts = 0i64;
        for k in 0..be(st.get(4..8)?) as usize {
            let n = be(st.get(8 + 8 * k..12 + 8 * k)?);
            let delta = be(st.get(12 + 8 * k..16 + 8 * k)?) as i64;
            for _ in 0..n {
                if times.len() >= count {
                    break;
                }
                times.push(dts);
                dts += delta;
            }
        }
    }
    if let Some(ct) = t.get(b"ctts") {
        if ct.len() >= 8 {
            let signed = ct[0] == 1;
            let mut j = 0;
            for k in 0..be(&ct[4..8]) as usize {
                let n = be(ct.get(8 + 8 * k..12 + 8 * k)?);
                let raw = be(ct.get(12 + 8 * k..16 + 8 * k)?) as u32;
                let off = if signed { raw as i32 as i64 } else { raw as i64 };
                for _ in 0..n {
                    if j < times.len() {
                        times[j] += off;
                    }
                    j += 1;
                }
            }
        }
    }
    let n = offsets.len().min(times.len()).min(count);
    let t0 = times[..n].iter().copied().min().unwrap_or(0);
    Some((0..n).map(|k| ((times[k] - t0) as f64 * 1000.0 / timescale as f64, offsets[k], sizes[k], nal_len)).collect())
}

fn scan_mp4(f: &mut File, size: u64, sh: &Shared) {
    for (k, (ms, off, n, nal_len)) in mp4_samples(f, size).into_iter().enumerate() {
        if sh.stop.load(Ordering::Relaxed) {
            return;
        }
        match read_at(f, off, (n as usize).min(SAMPLE_HEAD)).and_then(|h| from_sample(&h, nal_len)) {
            Some(m) => sh.add(ms, m),
            None if k >= PROBE_SAMPLES && !sh.any() => return,
            None => {}
        }
    }
}

// Matroska
fn vint(f: &mut File, keep_marker: bool) -> Option<(u64, bool, usize)> {
    let mut b = [0u8; 1];
    f.read_exact(&mut b).ok()?;
    let first = b[0];
    let mut n = 1;
    let mut mask = 0x80u8;
    while n <= 8 && first & mask == 0 {
        mask >>= 1;
        n += 1;
    }
    if n > 8 {
        return None;
    }
    let mut rest = vec![0u8; n - 1];
    f.read_exact(&mut rest).ok()?;
    let mut v = if keep_marker { first as u64 } else { (first & mask.wrapping_sub(1)) as u64 };
    for x in rest {
        v = (v << 8) | x as u64;
    }
    let unknown = !keep_marker && v == (1u64 << (7 * n)) - 1;
    Some((v, unknown, n))
}

fn children(f: &mut File, start: u64, end: u64, budget: &mut usize) -> Vec<(u64, u64, u64)> {
    let mut out = Vec::new();
    let mut pos = start;
    while pos < end && *budget > 0 {
        *budget -= 1;
        if f.seek(SeekFrom::Start(pos)).is_err() {
            break;
        }
        let Some((id, _, a)) = vint(f, true) else { break };
        let Some((size, unknown, b)) = vint(f, false) else { break };
        let body = pos + (a + b) as u64;
        let size = if unknown { end.saturating_sub(body) } else { size };
        out.push((id, body, (body + size).min(end)));
        pos = body + size;
    }
    out
}

fn uint_at(f: &mut File, a: u64, b: u64) -> u64 {
    read_at(f, a, ((b - a) as usize).min(8)).map(|v| be(&v)).unwrap_or(0)
}

fn mkv_block(f: &mut File, cid: u64, a: u64, b: u64, track: u64, nal_len: usize, budget: &mut usize) -> (Option<Meta>, Option<i64>) {
    let (mut a, mut b) = (a, b);
    let mut extra = None;
    if cid == 0xA0 {
        let mut blk = None;
        for (gid, ga, gb) in children(f, a, b, budget) {
            if gid == 0xA1 {
                blk = Some((ga, gb));
            } else if gid == 0x75A1 {
                for (mid, ma, mb) in children(f, ga, gb, budget) {
                    if mid != 0xA6 {
                        continue;
                    }
                    let (mut add_id, mut data) = (1u64, Vec::new());
                    for (xid, xa, xb) in children(f, ma, mb, budget) {
                        if xid == 0xEE {
                            add_id = uint_at(f, xa, xb);
                        } else if xid == 0xA5 {
                            data = read_at(f, xa, ((xb - xa) as usize).min(4096)).unwrap_or_default();
                        }
                    }
                    if add_id == 4 && !data.is_empty() {
                        extra = parse_t35(&data);
                    }
                }
            }
        }
        match blk {
            Some((x, y)) => {
                a = x;
                b = y;
            }
            None => return (None, None),
        }
    }
    if f.seek(SeekFrom::Start(a)).is_err() {
        return (None, None);
    }
    let Some((num, _, k)) = vint(f, false) else { return (None, None) };
    if num != track {
        return (None, None);
    }
    let Some(hdr) = read_at(f, a + k as u64, 3) else { return (None, None) };
    if hdr.len() < 3 {
        return (None, None);
    }
    let rel = i16::from_be_bytes([hdr[0], hdr[1]]) as i64;
    if extra.is_some() {
        return (extra, Some(rel));
    }
    if hdr[2] & 0x06 != 0 {
        return (None, Some(rel));
    }
    let start = a + k as u64 + 3;
    let head = read_at(f, start, ((b.saturating_sub(start)) as usize).min(SAMPLE_HEAD)).unwrap_or_default();
    (from_sample(&head, nal_len), Some(rel))
}

fn scan_mkv(f: &mut File, size: u64, sh: &Shared) {
    let mut budget = MAX_BOXES * 50;
    for (eid, a, b) in children(f, 0, size, &mut budget) {
        if eid != 0x18538067 {
            continue;
        }
        let (mut scale, mut track, mut nal_len, mut blocks) = (1_000_000u64, 0u64, 4usize, 0usize);
        for (sid, sa, sb) in children(f, a, b, &mut budget) {
            if sh.stop.load(Ordering::Relaxed) {
                return;
            }
            if sid == 0x1549A966 {
                for (iid, ia, ib) in children(f, sa, sb, &mut budget) {
                    if iid == 0x2AD7B1 {
                        let v = uint_at(f, ia, ib);
                        if v > 0 {
                            scale = v;
                        }
                    }
                }
            } else if sid == 0x1654AE6B {
                for (tid, ta, tb) in children(f, sa, sb, &mut budget) {
                    if tid != 0xAE || track != 0 {
                        continue;
                    }
                    let (mut num, mut kind, mut codec, mut cp) = (0u64, 0u64, Vec::new(), Vec::new());
                    for (kid, ka, kb) in children(f, ta, tb, &mut budget) {
                        match kid {
                            0xD7 => num = uint_at(f, ka, kb),
                            0x83 => kind = uint_at(f, ka, kb),
                            0x86 => codec = read_at(f, ka, ((kb - ka) as usize).min(64)).unwrap_or_default(),
                            0x63A2 => cp = read_at(f, ka, ((kb - ka) as usize).min(1 << 16)).unwrap_or_default(),
                            _ => {}
                        }
                    }
                    if kind == 1 && codec.starts_with(b"V_MPEGH/ISO/HEVC") {
                        track = num;
                        if cp.len() > 21 {
                            nal_len = (cp[21] & 3) as usize + 1;
                        }
                    }
                }
            } else if sid == 0x1F43B675 && track != 0 {
                let mut ctime = 0u64;
                for (cid, ca, cb) in children(f, sa, sb, &mut budget) {
                    if sh.stop.load(Ordering::Relaxed) {
                        return;
                    }
                    if cid == 0xE7 {
                        ctime = uint_at(f, ca, cb);
                    } else if cid == 0xA3 || cid == 0xA0 {
                        let (m, rel) = mkv_block(f, cid, ca, cb, track, nal_len, &mut budget);
                        let Some(rel) = rel else { continue };
                        blocks += 1;
                        match m {
                            Some(m) => sh.add((ctime as i64 + rel) as f64 * scale as f64 / 1e6, m),
                            None if blocks >= PROBE_SAMPLES && !sh.any() => return,
                            None => {}
                        }
                    }
                }
                budget = budget.max(4000);
            }
        }
        return;
    }
}

fn run(path: String, sh: Arc<Shared>) {
    if let Ok(mut f) = File::open(&path) {
        let size = f.metadata().map(|m| m.len()).unwrap_or(0);
        let mut head = [0u8; 16];
        if f.read(&mut head).unwrap_or(0) >= 8 {
            if matches!(&head[4..8], b"ftyp" | b"moov" | b"mdat" | b"free" | b"wide" | b"skip") {
                scan_mp4(&mut f, size, &sh);
            } else if head[..4] == [0x1A, 0x45, 0xDF, 0xA3] {
                scan_mkv(&mut f, size, &sh);
            }
        }
    }
    sh.done.store(true, Ordering::Release);
}

// ------------------------------------------------------------------ Python
#[pyclass]
pub struct Hdr10PlusReader {
    sh: Arc<Shared>,
    thread: Option<std::thread::JoinHandle<()>>,
}

fn to_dict<'py>(py: Python<'py>, m: &Meta) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("peak", m.peak)?;
    d.set_item("p99", m.p99)?;
    d.set_item("avg", m.avg)?;
    d.set_item("target", m.target)?;
    d.set_item("knee", m.knee)?;
    d.set_item("anchors", m.anchors.clone())?;
    Ok(d)
}

#[pymethods]
impl Hdr10PlusReader {
    #[new]
    fn new(path: String) -> Self {
        let sh = Arc::new(Shared { tl: Mutex::new(Timeline::default()), stop: AtomicBool::new(false), done: AtomicBool::new(false) });
        let s2 = sh.clone();
        let thread = std::thread::Builder::new().name("hdr10plus".into()).spawn(move || run(path, s2)).ok();
        if thread.is_none() {
            sh.done.store(true, Ordering::Release);
        }
        Hdr10PlusReader { sh, thread }
    }

    #[getter]
    fn done(&self) -> bool {
        self.sh.done.load(Ordering::Acquire)
    }

    fn found(&self) -> bool {
        self.sh.any()
    }

    fn scenes(&self) -> usize {
        self.sh.tl.lock().unwrap().items.len()
    }

    /// Index of the scene at that time (-1: none known), so the caller can tell changes apart.
    fn index_at(&self, ms: f64) -> i64 {
        self.sh.tl.lock().unwrap().index_at(ms).map(|i| i as i64).unwrap_or(-1)
    }

    fn scene<'py>(&self, py: Python<'py>, i: usize) -> PyResult<Option<Bound<'py, PyDict>>> {
        let tl = self.sh.tl.lock().unwrap();
        match tl.items.get(i) {
            Some(m) => Ok(Some(to_dict(py, m)?)),
            None => Ok(None),
        }
    }

    fn timeline(&self) -> Vec<f64> {
        self.sh.tl.lock().unwrap().times.clone()
    }

    fn stop(&self) {
        self.sh.stop.store(true, Ordering::Relaxed);
    }

    #[pyo3(signature = (timeout=None))]
    fn join(&mut self, py: Python<'_>, timeout: Option<f64>) {
        let end = timeout.map(|t| std::time::Instant::now() + std::time::Duration::from_secs_f64(t));
        py.allow_threads(|| {
            while !self.sh.done.load(Ordering::Acquire) {
                if end.map(|e| std::time::Instant::now() >= e).unwrap_or(false) {
                    return;
                }
                std::thread::sleep(std::time::Duration::from_millis(5));
            }
        });
        if self.sh.done.load(Ordering::Acquire) {
            if let Some(t) = self.thread.take() {
                let _ = t.join();
            }
        }
    }
}
