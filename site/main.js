// Latest release from GitHub → download links, version, size and SHA-256 on the page.
const REPO = "LucsLabsStudios/refrata";
// the page language (pt, en, es) picks the wording of the lines this script fills in
const LANG = (document.documentElement.lang || "pt").slice(0, 2);
const T = {
  pt: { size: (v, mb) => `versão ${v} · ${mb} MB`, line: (v) => `Versão ${v} · Windows 10 e 11`, copy: "Copiar", copied: "Copiado ✓" },
  en: { size: (v, mb) => `version ${v} · ${mb} MB`, line: (v) => `Version ${v} · Windows 10 and 11`, copy: "Copy", copied: "Copied ✓" },
  es: { size: (v, mb) => `versión ${v} · ${mb} MB`, line: (v) => `Versión ${v} · Windows 10 y 11`, copy: "Copiar", copied: "Copiado ✓" },
}[LANG] || null;
const t = T || { size: (v, mb) => `versão ${v} · ${mb} MB`, line: (v) => `Versão ${v} · Windows 10 e 11`, copy: "Copiar", copied: "Copiado ✓" };

async function latest() {
  try {
    const r = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!r.ok) return;
    const rel = await r.json();
    const version = String(rel.tag_name || "").replace(/^v/i, "");
    const setup = (rel.assets || []).find((a) => /^Refrata-Setup-[\d.]+\.exe$/i.test(a.name));
    const zip = (rel.assets || []).find((a) => /portable\.zip$/i.test(a.name));
    if (setup) {
      document.querySelectorAll("[data-download]").forEach((a) => (a.href = setup.browser_download_url));
      const mb = Math.round(setup.size / 1e6);
      document.querySelectorAll("[data-size]").forEach((el) => (el.textContent = t.size(version, mb)));
      const sha = String(setup.digest || "").replace(/^sha256:/i, "");
      if (sha) {
        document.querySelectorAll("[data-sha]").forEach((el) => {
          el.innerHTML = ` · SHA-256 <code></code> <button type="button" class="copy-sha"></button>`;
          el.querySelector("code").textContent = sha;
          const btn = el.querySelector(".copy-sha");
          btn.textContent = t.copy;
          let timer = 0;
          btn.addEventListener("click", async () => {
            try {
              await navigator.clipboard.writeText(sha);
              btn.textContent = t.copied;
              btn.classList.add("copied");
              clearTimeout(timer);
              timer = setTimeout(() => {
                btn.textContent = t.copy;
                btn.classList.remove("copied");
              }, 2000);
            } catch (_) { /* clipboard unavailable */ }
          });
        });
      }
    }
    if (zip) document.querySelectorAll("[data-portable]").forEach((a) => (a.href = zip.browser_download_url));
    const deb = (rel.assets || []).find((a) => /_amd64\.deb$/i.test(a.name));
    if (deb) document.querySelectorAll("[data-linux]").forEach((a) => (a.href = deb.browser_download_url));
    if (version) {
      document.querySelectorAll("[data-version]").forEach((el) => (el.textContent = version));
      document.querySelectorAll("[data-version-line]").forEach((el) => (el.textContent = t.line(version)));
    }
  } catch (_) {
    /* offline or rate-limited: the links keep pointing at the releases page */
  }
}
latest();

// ------------------------------------------------------------------ motion
// Four moments carry the page: the hero film closing into the player, the picture reel, the subtitle
// scene and the Windows moments. Chapter titles rise once; body text never moves. Demos only run while
// they are on screen, and nothing loops off screen.
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

// reveal: chapter titles and the big numbers, one after the other
$$(".ch-head > .eyebrow, .ch-head > h2, .split-copy > .eyebrow, .split-copy > h2, .facts dl > div, .dl-copy > h2").forEach((el) => {
  el.classList.add("rv");
  el.style.setProperty("--i", Math.min([...el.parentElement.children].indexOf(el), 6));
});
$$(".music-stack > img").forEach((el, i) => el.style.setProperty("--i", i));

const once = new IntersectionObserver((entries) => entries.forEach((e) => {
  if (!e.isIntersecting) return;
  e.target.classList.add("in");
  once.unobserve(e.target);
}), { threshold: 0.18, rootMargin: "0px 0px -8% 0px" });
$$(".rv, .reveal-img, .steps[data-scene]").forEach((el) => once.observe(el));

// run something only while it is on screen
function whileVisible(el, start, stop, threshold = 0.25) {
  let on = false;
  new IntersectionObserver(([e]) => {
    if (e.isIntersecting && !on) { on = true; start(); }
    else if (!e.isIntersecting && on) { on = false; stop(); }
  }, { threshold }).observe(el);
}

// ------------------------------------------------------------------ hero: where the film lands
// The CSS shrinks the film into the player's frame; it needs the scale between the two boxes.
const hero = $("[data-hero]");
function measureHero() {
  if (!hero) return;
  const pin = $(".hero-pin", hero);
  if (getComputedStyle(pin).position !== "sticky") return;
  const film = $(".hero-film", hero), player = $(".hero-player", hero);
  hero.style.setProperty("--fs", (player.offsetWidth / film.offsetWidth).toFixed(4));
  hero.style.setProperty("--fy", "34px");
}
measureHero();

// ------------------------------------------------------------------ scrubber: the page as a timeline
const scrub = $(".scrub");
const chapters = $$("[data-chapter]");
const navLinks = $$(".links a");
const DURATION = 180; // the page "runs" three minutes
const clock = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
let marks = [];
function measureScrub() {
  if (!scrub) return;
  const max = document.documentElement.scrollHeight - innerHeight;
  marks = chapters.map((el) => ({ el, name: el.dataset.chapter, at: Math.max(0, Math.min(1, (el.getBoundingClientRect().top + scrollY - 80) / max)) }));
  const box = $(".scrub-marks", scrub);
  box.textContent = "";
  marks.forEach((m) => { const i = document.createElement("i"); i.style.left = (m.at * 100).toFixed(2) + "%"; box.append(i); });
}
function chapterAt(p) {
  let cur = null;
  for (const m of marks) if (m.at <= p + 0.001) cur = m;
  return cur;
}
let ticking = false;
function onScroll() {
  if (ticking || !scrub) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
    const max = document.documentElement.scrollHeight - innerHeight;
    const p = max > 0 ? Math.min(1, scrollY / max) : 0;
    $(".scrub-fill", scrub).style.setProperty("--p", p.toFixed(4));
    $(".scrub-time", scrub).textContent = `${clock(p * DURATION)} / ${clock(DURATION)}`;
    scrub.classList.toggle("on", scrollY > innerHeight * 0.9);
    const cur = chapterAt(p);
    navLinks.forEach((a) => a.classList.toggle("here", !!cur && a.getAttribute("href") === "#" + cur.el.id));
  });
}
if (scrub) {
  const track = $(".scrub-track", scrub), tip = $(".scrub-tip", scrub);
  const frac = (e) => { const r = track.getBoundingClientRect(); return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)); };
  // hovering shows where a click would take you, like the player's seek bar
  track.addEventListener("pointermove", (e) => {
    const f = frac(e), cur = chapterAt(f);
    tip.textContent = clock(f * DURATION) + (cur ? " · " + cur.name : "");
    tip.style.left = (f * 100).toFixed(2) + "%";
  });
  track.addEventListener("click", (e) => {
    const max = document.documentElement.scrollHeight - innerHeight;
    window.scrollTo({ top: frac(e) * max, behavior: reduce ? "auto" : "smooth" });
  });
  measureScrub();
  addEventListener("scroll", onScroll, { passive: true });
  onScroll();
}
addEventListener("load", () => { measureHero(); measureScrub(); onScroll(); });
let resizeT = 0;
addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { measureHero(); measureScrub(); onScroll(); }, 120); }, { passive: true });

// ------------------------------------------------------------------ 01 picture reel: one frame, four passes
const reel = $("[data-reel]");
if (reel) {
  const frame = $(".frame", reel);
  const steps = $$(".reel-steps article", reel);
  const dots = $$(".reel-dots li", reel);
  const range = $(".f-range", frame);
  let step = 0;

  // SDR | HDR: a native range input does the dragging, so touch and keyboard work too
  const setPos = () => frame.style.setProperty("--pos", range.value + "%");
  range.addEventListener("input", () => { frame.classList.remove("hint"); setPos(); });
  range.addEventListener("pointerdown", () => { frame.classList.remove("hint"); frame.classList.add("dragging"); });
  addEventListener("pointerup", () => frame.classList.remove("dragging"));
  frame.addEventListener("animationend", () => frame.classList.remove("hint"));
  let hinted = false;

  // HDR10+: the brightness target changes scene by scene
  const scenes = [[214, 1000], [215, 280], [216, 4000], [217, 650], [218, 1600]];
  let sceneI = 0, sceneT = 0;
  const showScene = () => {
    const [n, nits] = scenes[sceneI % scenes.length];
    $("[data-scene-n]", frame).textContent = n;
    $("[data-nits]", frame).textContent = nits.toLocaleString(document.documentElement.lang);
    $("[data-nitbar]", frame).style.transform = `scaleX(${Math.max(0.06, nits / 4000)})`;
    frame.style.setProperty("--lum", (0.82 + Math.sqrt(nits / 4000) * 0.3).toFixed(3));
    sceneI++;
  };

  // smooth motion: one eased path, sampled 24 times a second above and every frame below
  const [d24, d60] = $$(".dot", frame);
  let raf = 0, t0 = 0, span = 0;
  const period = 1800;
  const ease = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
  const at = (ms) => { const p = (ms % (period * 2)) / period; return ease(p <= 1 ? p : 2 - p) * span; };
  const frameLoop = (now) => {
    const ms = now - t0;
    d60.style.transform = `translateX(${at(ms).toFixed(1)}px)`;
    d24.style.transform = `translateX(${at(Math.floor(ms / (1000 / 24)) * (1000 / 24)).toFixed(1)}px)`;
    raf = requestAnimationFrame(frameLoop);
  };

  // RTX Super Resolution: flips on its own until someone uses the switch
  const srBtn = $(".sr-toggle", frame);
  let srT = 0, srTouched = false;
  const flip = (on) => { frame.classList.toggle("sd", !on); srBtn.setAttribute("aria-pressed", String(on)); };
  srBtn.addEventListener("click", () => { srTouched = true; clearInterval(srT); flip(srBtn.getAttribute("aria-pressed") !== "true"); });

  let visible = false;
  function run() {
    clearInterval(sceneT); cancelAnimationFrame(raf); clearInterval(srT);
    if (!visible || reduce) return;
    if (step === 1 && !hinted) { hinted = true; frame.classList.add("hint"); }
    if (step === 2) { showScene(); sceneT = setInterval(showScene, 1500); }
    if (step === 3) { span = d24.parentElement.clientWidth - 48 - d24.offsetWidth; t0 = performance.now(); raf = requestAnimationFrame(frameLoop); }
    if (step === 4 && !srTouched) { flip(true); srT = setInterval(() => flip(srBtn.getAttribute("aria-pressed") !== "true"), 2200); }
  }
  function go(n) {
    if (n === step) return;
    step = n;
    frame.dataset.step = n;
    steps.forEach((s, i) => s.classList.toggle("on", i === n - 1));
    dots.forEach((d, i) => d.classList.toggle("on", i === n - 1));
    range.disabled = n !== 1;
    srBtn.tabIndex = n === 4 ? 0 : -1;
    if (n !== 4 && !srTouched) flip(true);
    run();
  }
  // the step whose text crosses the middle of the screen drives the frame
  const pick = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (e.isIntersecting) go(Number(e.target.dataset.go));
  }), { rootMargin: "-45% 0px -45% 0px" });
  steps.forEach((s) => pick.observe(s));
  go(1);
  whileVisible(frame, () => { visible = true; run(); }, () => { visible = false; run(); }, 0.3);
  showScene();
}

// ------------------------------------------------------------------ Windows moments: tabs
$$(".moments").forEach((sec) => {
  const tabs = $$('[role="tab"]', sec);
  const ink = $(".tab-ink", sec);
  const current = () => tabs.find((t) => t.getAttribute("aria-selected") === "true");
  const place = (t) => { ink.style.setProperty("--x", t.offsetLeft - 4 + "px"); ink.style.setProperty("--w", t.offsetWidth + "px"); };
  const play = (pane) => {
    const scene = $(".scene", pane);
    if (!scene) return;
    scene.classList.remove("in");
    void scene.offsetWidth; // restart the scene's story
    scene.classList.add("in");
  };
  let seen = false;
  const select = (t, focus) => {
    tabs.forEach((x) => {
      const on = x === t;
      x.setAttribute("aria-selected", String(on));
      x.tabIndex = on ? 0 : -1;
      const pane = document.getElementById(x.getAttribute("aria-controls"));
      pane.hidden = !on;
      pane.classList.toggle("show", on);
      if (on && seen) play(pane);
    });
    place(t);
    if (focus) t.focus();
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(t));
    t.addEventListener("keydown", (e) => {
      const k = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (k) { e.preventDefault(); select(tabs[(i + k + tabs.length) % tabs.length], true); }
      if (e.key === "Home") { e.preventDefault(); select(tabs[0], true); }
      if (e.key === "End") { e.preventDefault(); select(tabs[tabs.length - 1], true); }
    });
  });
  place(tabs[0]);
  addEventListener("resize", () => place(current()), { passive: true });
  if (document.fonts) document.fonts.ready.then(() => place(current()));
  // the first scene plays when the stage comes into view
  new IntersectionObserver(([e], obs) => {
    if (!e.isIntersecting) return;
    seen = true; obs.disconnect();
    play(document.getElementById(current().getAttribute("aria-controls")));
  }, { threshold: 0.35 }).observe($(".panes", sec));
});

// ------------------------------------------------------------------ AI subtitles: written word by word, the way the model streams it
$$("[data-subs]").forEach((fig) => {
  const lines = $$("[data-line]", fig);
  lines.forEach((line) => {
    const words = line.textContent.trim().split(/\s+/);
    line.textContent = "";
    words.forEach((w, k) => {
      const s = document.createElement("span");
      s.className = "w"; s.style.setProperty("--k", k); s.textContent = w;
      line.append(s, " ");
    });
  });
  if (reduce) { lines[0].classList.add("on"); return; }
  let idx = -1, timer = 0;
  const next = () => {
    if (idx >= 0) { const old = lines[idx]; old.classList.add("out"); setTimeout(() => old.classList.remove("on", "out"), 220); }
    idx = (idx + 1) % lines.length;
    const cur = lines[idx];
    setTimeout(() => cur.classList.add("on"), 260);
    timer = setTimeout(next, 2600 + cur.children.length * 110);
  };
  whileVisible(fig, () => { fig.classList.add("live"); if (!timer) next(); }, () => {
    fig.classList.remove("live"); clearTimeout(timer); timer = 0;
  });
});

// language switch: remember the choice so the root page stops redirecting
document.querySelectorAll(".lang-switch a").forEach((a) =>
  a.addEventListener("click", () => {
    try { localStorage.setItem("refrata-lang", a.dataset.lang); } catch (_) { /* private mode */ }
  })
);
