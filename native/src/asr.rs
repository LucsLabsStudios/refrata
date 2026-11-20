//! Speech recognition for subtitles made from the sound (refrata/aisubs.py): whisper.cpp, compiled
//! into this module (whisper-rs), so the app runs no downloaded program, only reads the model files.
//! All the work happens with the GIL released.
//!
//! - `Vad`: Silero voice activity detection (whisper.cpp's), to find where people speak. Only speech
//!   is sent to Whisper, cut at pauses: silence and music are where Whisper invents sentences, and a
//!   piece cut in the middle of a word loses it.
//! - `Transcriber`: Whisper itself, with DTW word times (the cross-attention alignment, far closer to
//!   the speech than Whisper's own timestamp tokens, which drift by up to seconds), beam search and
//!   the previous sentence as context.

use pyo3::prelude::*;
use std::sync::Mutex;
use whisper_rs::{
    DtwMode, DtwModelPreset, DtwParameters, FullParams, SamplingStrategy, WhisperContext,
    WhisperContextParameters, WhisperVadContext, WhisperVadContextParams, WhisperVadParams,
};

fn pcm_to_f32(pcm: &[u8]) -> Vec<f32> {
    pcm.chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]]) as f32 / 32768.0).collect()
}

fn err<E: std::fmt::Display>(e: E) -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err(format!("{e}"))
}

#[pyclass]
pub struct Transcriber {
    ctx: Mutex<WhisperContext>,
    dtw: bool,
}

#[pymethods]
impl Transcriber {
    /// `model`: a ggml model file (ggml-base-q8_0.bin...). `dtw`: the model's alignment heads for
    /// word times ("base", "medium", "large-v3-turbo"; "" for none).
    #[new]
    #[pyo3(signature = (model, dtw="".to_string()))]
    fn new(py: Python<'_>, model: String, dtw: String) -> PyResult<Self> {
        let preset = match dtw.as_str() {
            "base" => Some(DtwModelPreset::Base),
            "medium" => Some(DtwModelPreset::Medium),
            "large-v3-turbo" => Some(DtwModelPreset::LargeV3Turbo),
            _ => None,
        };
        let dtw = preset.is_some();
        let ctx = py
            .allow_threads(|| {
                let mut cp = WhisperContextParameters::default();
                if let Some(p) = preset {
                    cp.flash_attn(false); // DTW is off with flash attention
                    cp.dtw_parameters(DtwParameters {
                        mode: DtwMode::ModelPreset { model_preset: p },
                        ..DtwParameters::default()
                    });
                }
                WhisperContext::new_with_params(&model, cp)
            })
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("whisper model: {e}")))?;
        Ok(Transcriber { ctx: Mutex::new(ctx), dtw })
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
            let samples = pcm_to_f32(&pcm);
            let mut state = ctx.create_state().map_err(err)?;
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
            state.full(p, &samples).map_err(err)?;
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

    /// Word by word: [(start ms, end ms, word)], times counted from `offset_ms`. A word starting one
    /// of whisper's segments (about a sentence) is flagged with a leading "\n".
    ///
    /// Word starts come from DTW when the model was opened with it, else from whisper's token times;
    /// a word ends where the next starts, or at its segment's end, never later than its length
    /// spoken at a usual pace. `prompt`: the text just before (context: names, spelling, punctuation
    /// carry over); `beam`: beam search width (1: greedy). Segments that are likely not speech
    /// (whisper's no-speech probability high and its own words unsure) are dropped.
    #[pyo3(signature = (pcm, offset_ms=0, lang="auto".to_string(), translate=false, threads=4,
                        prompt="".to_string(), beam=1))]
    #[allow(clippy::too_many_arguments)]
    fn transcribe_words(&self, py: Python<'_>, pcm: Vec<u8>, offset_ms: i64, lang: String, translate: bool,
                        threads: i32, prompt: String, beam: i32) -> PyResult<(Vec<(i64, i64, String)>, String)> {
        let dtw = self.dtw;
        py.allow_threads(|| {
            let ctx = self.ctx.lock().unwrap_or_else(|e| e.into_inner());
            let samples = pcm_to_f32(&pcm);
            let mut state = ctx.create_state().map_err(err)?;
            let strategy = if beam > 1 {
                SamplingStrategy::BeamSearch { beam_size: beam, patience: -1.0 }
            } else {
                SamplingStrategy::Greedy { best_of: 1 }
            };
            let mut p = FullParams::new(strategy);
            p.set_n_threads(threads.max(1));
            p.set_translate(translate);
            p.set_language(Some(if lang.is_empty() { "auto" } else { lang.as_str() }));
            p.set_print_progress(false);
            p.set_print_realtime(false);
            p.set_print_special(false);
            p.set_print_timestamps(false);
            p.set_suppress_blank(true);
            p.set_suppress_nst(true); // no "[Music]", "♪" and the like
            p.set_no_context(true);
            if !prompt.is_empty() {
                p.set_initial_prompt(&prompt);
            }
            p.set_single_segment(false);
            p.set_token_timestamps(true);
            state.full(p, &samples).map_err(err)?;
            let eot = ctx.token_eot();
            let total_ms = samples.len() as i64 * 1000 / 16000;
            // bytes until the end: whisper splits a character of two UTF-8 bytes (á, é, ç) over two
            // tokens, and decoding each token alone turned "está" into "est" + garbage
            struct W { t0: i64, t1: i64, seg_end: i64, bytes: Vec<u8> }
            let mut raw: Vec<W> = Vec::new();
            let n = state.full_n_segments();
            for i in 0..n {
                let Some(seg) = state.get_segment(i) else { continue };
                let whole = seg.to_str_lossy().map(|s| s.trim().to_string()).unwrap_or_default();
                if whole.is_empty() || (whole.starts_with('[') && whole.ends_with(']'))
                    || (whole.starts_with('(') && whole.ends_with(')')) || whole.starts_with('♪') {
                    continue; // [Music], (applause), ♪: not speech
                }
                let (s0, s1) = (seg.start_timestamp() * 10, (seg.end_timestamp() * 10).min(total_ms));
                // likely not speech: whisper thinks so and its words are unsure (the usual signature of
                // a sentence made up over noise)
                let mut psum = 0.0f32;
                let mut pn = 0;
                for j in 0..seg.n_tokens() {
                    if let Some(tok) = seg.get_token(j) {
                        if tok.token_id() < eot {
                            psum += tok.token_data().p;
                            pn += 1;
                        }
                    }
                }
                let pavg = if pn > 0 { psum / pn as f32 } else { 0.0 };
                if seg.no_speech_probability() > 0.6 && pavg < 0.55 {
                    continue;
                }
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
                    let mut t0 = (d.t0 * 10).clamp(s0, s1.max(s0));
                    if dtw && d.t_dtw >= 0 {
                        t0 = (d.t_dtw * 10).clamp(s0, s1.max(s0));
                    }
                    let t1 = (d.t1 * 10).clamp(t0, s1.max(t0));
                    if bytes[0] == b' ' || raw.is_empty() || first {
                        let mut w = if first { b"\n".to_vec() } else { Vec::new() };
                        w.extend(bytes.iter().skip_while(|c| **c == b' '));
                        raw.push(W { t0, t1, seg_end: s1, bytes: w });
                    } else if let Some(last) = raw.last_mut() {
                        last.t1 = last.t1.max(t1); // a piece of the word before ("trans" + "lation")
                        last.bytes.extend_from_slice(&bytes);
                    }
                    first = false;
                }
            }
            // ends: with DTW the start of the next word (or the segment's end), capped by the word's
            // length at a usual pace, so a word before a pause does not stay on through the pause
            let mut words: Vec<(i64, i64, String)> = Vec::with_capacity(raw.len());
            for k in 0..raw.len() {
                let text = String::from_utf8_lossy(&raw[k].bytes).into_owned();
                let chars = text.trim().chars().count() as i64;
                let t0 = raw[k].t0;
                let mut t1 = if dtw {
                    let next = raw.get(k + 1).map(|w| w.t0).unwrap_or(raw[k].seg_end);
                    next.min(raw[k].seg_end).max(t0)
                } else {
                    raw[k].t1
                };
                t1 = t1.min(t0 + 200 + 90 * chars).max(t0 + 60);
                words.push((offset_ms + t0, offset_ms + t1, text));
            }
            let lang_id = state.full_lang_id_from_state();
            let detected = whisper_rs::get_lang_str(lang_id).unwrap_or("").to_string();
            Ok((words, detected))
        })
    }
}

/// Where people speak in a stretch of sound (Silero VAD, ggml-silero-v5.1.2.bin, shipped with the app).
#[pyclass]
pub struct Vad {
    ctx: Mutex<WhisperVadContext>,
}

#[pymethods]
impl Vad {
    #[new]
    fn new(py: Python<'_>, model: String) -> PyResult<Self> {
        let ctx = py
            .allow_threads(|| {
                let mut cp = WhisperVadContextParams::default();
                cp.set_n_threads(2);
                cp.set_use_gpu(false); // a tiny model: the CPU does it in milliseconds
                WhisperVadContext::new(&model, cp)
            })
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("vad model: {e}")))?;
        Ok(Vad { ctx: Mutex::new(ctx) })
    }

    /// 16 kHz mono signed 16-bit PCM -> [(start ms, end ms)] of speech, counted from `offset_ms`.
    /// `min_silence_ms`: a pause at least this long ends a stretch of speech; `pad_ms` is kept
    /// around each, so first and last syllables are not cut.
    #[pyo3(signature = (pcm, offset_ms=0, threshold=0.5, min_silence_ms=300, pad_ms=200, max_speech_s=20.0))]
    fn speech(&self, py: Python<'_>, pcm: Vec<u8>, offset_ms: i64, threshold: f32, min_silence_ms: i32,
              pad_ms: i32, max_speech_s: f32) -> PyResult<Vec<(i64, i64)>> {
        py.allow_threads(|| {
            let mut ctx = self.ctx.lock().unwrap_or_else(|e| e.into_inner());
            let samples = pcm_to_f32(&pcm);
            let mut vp = WhisperVadParams::new();
            vp.set_threshold(threshold);
            vp.set_min_speech_duration(250);
            vp.set_min_silence_duration(min_silence_ms);
            vp.set_speech_pad(pad_ms);
            vp.set_max_speech_duration(max_speech_s);
            vp.set_samples_overlap(0.0);
            let segs = ctx.segments_from_samples(vp, &samples).map_err(err)?;
            let total = samples.len() as i64 * 1000 / 16000;
            Ok(segs
                .map(|s| {
                    let a = ((s.start * 10.0) as i64).clamp(0, total);
                    let b = ((s.end * 10.0) as i64).clamp(a, total);
                    (offset_ms + a, offset_ms + b)
                })
                .collect())
        })
    }
}
