//! Speech recognition for subtitles made from the sound (refrata/aisubs.py): whisper.cpp, compiled
//! into this module (whisper-rs), so the app runs no downloaded program, only reads the model file.
//! All the work happens with the GIL released.

use pyo3::prelude::*;
use std::sync::Mutex;
use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

#[pyclass]
pub struct Transcriber {
    ctx: Mutex<WhisperContext>,
}

#[pymethods]
impl Transcriber {
    /// `model`: a ggml model file (ggml-base-q8_0.bin...).
    #[new]
    fn new(py: Python<'_>, model: String) -> PyResult<Self> {
        let ctx = py
            .allow_threads(|| WhisperContext::new_with_params(&model, WhisperContextParameters::default()))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("whisper model: {e}")))?;
        Ok(Transcriber { ctx: Mutex::new(ctx) })
    }

    /// 16 kHz mono signed 16-bit PCM -> [(start ms, end ms, text)], times counted from `offset_ms`.
    /// `lang`: "auto" or a code ("pt", "en"...); `translate`: into English.
    #[pyo3(signature = (pcm, offset_ms=0, lang="auto".to_string(), translate=false, threads=4))]
    fn transcribe(&self, py: Python<'_>, pcm: Vec<u8>, offset_ms: i64, lang: String, translate: bool,
                  threads: i32) -> PyResult<(Vec<(i64, i64, String)>, String)> {
        // the lock is taken with the GIL released: taken before, a second caller would hold the GIL
        // while waiting for it, and the first, done, could never take the GIL back (a deadlock that
        // froze the whole app when a new transcription started while one was running)
        py.allow_threads(|| {
            let ctx = self.ctx.lock().unwrap_or_else(|e| e.into_inner());
            let samples: Vec<f32> = pcm.chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0).collect();
            let mut state = ctx.create_state().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e}")))?;
            let mut p = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
            p.set_n_threads(threads.max(1));
            p.set_translate(translate);
            p.set_language(Some(if lang.is_empty() { "auto" } else { lang.as_str() }));
            p.set_print_progress(false);
            p.set_print_realtime(false);
            p.set_print_special(false);
            p.set_print_timestamps(false);
            p.set_suppress_blank(true);
            p.set_no_context(true);
            p.set_single_segment(false);
            state.full(p, &samples).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e}")))?;
            let n = state.full_n_segments();
            let mut out = Vec::new();
            for i in 0..n {
                let Some(seg) = state.get_segment(i) else { continue };
                let text = seg.to_str_lossy().map(|s| s.trim().to_string()).unwrap_or_default();
                if text.is_empty() || (text.starts_with('[') && text.ends_with(']')) || (text.starts_with('(') && text.ends_with(')')) {
                    continue; // [Music], (applause): not speech
                }
                out.push((offset_ms + seg.start_timestamp() * 10, offset_ms + seg.end_timestamp() * 10, text));
            }
            let lang_id = state.full_lang_id_from_state();
            let detected = whisper_rs::get_lang_str(lang_id).unwrap_or("").to_string();
            Ok((out, detected))
        })
    }

    /// Like `transcribe`, word by word: [(start ms, end ms, word)] with each word's own times
    /// (whisper's token timestamps), so subtitles can be cut where the speech is, not in the
    /// long blocks whisper returns. A word starting a sentence-sized segment is flagged with a
    /// leading "\n" (the segment boundary whisper itself chose).
    #[pyo3(signature = (pcm, offset_ms=0, lang="auto".to_string(), translate=false, threads=4))]
    fn transcribe_words(&self, py: Python<'_>, pcm: Vec<u8>, offset_ms: i64, lang: String, translate: bool,
                        threads: i32) -> PyResult<(Vec<(i64, i64, String)>, String)> {
        py.allow_threads(|| {
            let ctx = self.ctx.lock().unwrap_or_else(|e| e.into_inner());
            let samples: Vec<f32> = pcm.chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0).collect();
            let mut state = ctx.create_state().map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e}")))?;
            let mut p = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
            p.set_n_threads(threads.max(1));
            p.set_translate(translate);
            p.set_language(Some(if lang.is_empty() { "auto" } else { lang.as_str() }));
            p.set_print_progress(false);
            p.set_print_realtime(false);
            p.set_print_special(false);
            p.set_print_timestamps(false);
            p.set_suppress_blank(true);
            p.set_no_context(true);
            p.set_single_segment(false);
            p.set_token_timestamps(true);
            state.full(p, &samples).map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("{e}")))?;
            let eot = ctx.token_eot();
            // bytes until the end: whisper splits a character of two UTF-8 bytes (á, é, ç) over two
            // tokens, and decoding each token alone turned "está" into "est" + garbage
            let mut raw: Vec<(i64, i64, Vec<u8>)> = Vec::new();
            let n = state.full_n_segments();
            for i in 0..n {
                let Some(seg) = state.get_segment(i) else { continue };
                let whole = seg.to_str_lossy().map(|s| s.trim().to_string()).unwrap_or_default();
                if whole.is_empty() || (whole.starts_with('[') && whole.ends_with(']'))
                    || (whole.starts_with('(') && whole.ends_with(')')) {
                    continue; // [Music], (applause): not speech
                }
                let (s0, s1) = (seg.start_timestamp() * 10, seg.end_timestamp() * 10);
                let mut first = true;
                for j in 0..seg.n_tokens() {
                    let Some(tok) = seg.get_token(j) else { continue };
                    if tok.token_id() >= eot {
                        continue; // timestamps, start/end markers: not text
                    }
                    let bytes = tok.to_bytes().map(|b| b.to_vec()).unwrap_or_default();
                    if bytes.is_empty() {
                        continue;
                    }
                    let d = tok.token_data();
                    // token times are 10 ms steps; out of the segment's range they are not to be trusted
                    let t0 = (d.t0 * 10).clamp(s0, s1.max(s0));
                    let t1 = (d.t1 * 10).clamp(t0, s1.max(t0));
                    if bytes[0] == b' ' || raw.is_empty() || first {
                        let mut w = if first { b"\n".to_vec() } else { Vec::new() };
                        w.extend(bytes.iter().skip_while(|c| **c == b' '));
                        raw.push((offset_ms + t0, offset_ms + t1, w));
                    } else if let Some(last) = raw.last_mut() {
                        last.1 = offset_ms + t1; // a piece of the word before ("trans" + "lation")
                        last.2.extend_from_slice(&bytes);
                    }
                    first = false;
                }
            }
            let words: Vec<(i64, i64, String)> = raw
                .into_iter()
                .map(|(a, b, w)| (a, b, String::from_utf8_lossy(&w).into_owned()))
                .collect();
            let lang_id = state.full_lang_id_from_state();
            let detected = whisper_rs::get_lang_str(lang_id).unwrap_or("").to_string();
            Ok((words, detected))
        })
    }
}
