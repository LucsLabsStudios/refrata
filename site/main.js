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
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

// reveal while scrolling: native scroll-driven CSS when available, IntersectionObserver otherwise.
// Items in the same group get an index so they arrive one after the other.
document.querySelectorAll(".grid, .steps, .policy, .ticks, .duo, .split-copy").forEach((group) => {
  [...group.children].forEach((el, i) => el.style.setProperty("--i", i));
});
const revealables = document.querySelectorAll(
  ".section > .eyebrow, .section > h2, .grid > *, .steps > *, .policy > *, .music-copy > *, .music-visual, .split-copy > *, .split-shot, .duo > *, .download, .formats, .faq > *, .section-lead");
const native = CSS.supports("animation-timeline: view()");
if (!reduce) {
  revealables.forEach((el) => el.classList.add("reveal"));
  if (!native && "IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { threshold: 0.15, rootMargin: "0px 0px -6% 0px" });
    revealables.forEach((el) => io.observe(el));
  } else if (!native) {
    revealables.forEach((el) => el.classList.add("in"));
  }
}

// hero window tilts toward the pointer, smoothed every frame (lerp), idle when settled
let vw = window.innerWidth, vh = window.innerHeight;
const win = document.querySelector(".hero-shot .window");
const tilt = document.querySelectorAll(".hero-shot .window");
let winRect = win ? win.getBoundingClientRect() : null;
window.addEventListener("resize", () => {
  vw = window.innerWidth;
  vh = window.innerHeight;
  if (win) winRect = win.getBoundingClientRect();
}, { passive: true });

if (win && finePointer && !reduce) {
  const rest = { x: 4, y: -9 };
  let target = { ...rest }, cur = { ...rest }, raf = 0;
  const tick = () => {
    cur.x += (target.x - cur.x) * 0.08;
    cur.y += (target.y - cur.y) * 0.08;
    for (const w of tilt) {
      w.style.setProperty("--rx", cur.x.toFixed(3) + "deg");
      w.style.setProperty("--ry", cur.y.toFixed(3) + "deg");
    }
    raf = Math.abs(target.x - cur.x) + Math.abs(target.y - cur.y) > 0.01 ? requestAnimationFrame(tick) : 0;
  };
  const kick = () => { if (!raf) raf = requestAnimationFrame(tick); };
  const hero = document.querySelector(".hero");
  hero.addEventListener("pointerenter", () => { winRect = win.getBoundingClientRect(); }, { passive: true });
  hero.addEventListener("pointermove", (e) => {
    const r = winRect || (winRect = win.getBoundingClientRect());
    const nx = (e.clientX - (r.left + r.width / 2)) / vw;
    const ny = (e.clientY - (r.top + r.height / 2)) / vh;
    target = { x: rest.x - ny * 8, y: rest.y + nx * 14 };
    kick();
  }, { passive: true });
  hero.addEventListener("pointerleave", () => { target = { ...rest }; kick(); });
}

// card spotlight follows the cursor (cache rect on pointerenter to avoid layout reads per move)
if (finePointer) {
  document.querySelectorAll(".card").forEach((card) => {
    let r = null;
    card.addEventListener("pointerenter", () => { r = card.getBoundingClientRect(); }, { passive: true });
    card.addEventListener("pointermove", (e) => {
      if (!r) r = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${e.clientX - r.left}px`);
      card.style.setProperty("--my", `${e.clientY - r.top}px`);
    }, { passive: true });
    card.addEventListener("pointerleave", () => { r = null; }, { passive: true });
  });
}

// language switch: remember the choice so the root page stops redirecting
document.querySelectorAll(".lang-switch a").forEach((a) =>
  a.addEventListener("click", () => {
    try { localStorage.setItem("refrata-lang", a.dataset.lang); } catch (_) { /* private mode */ }
  })
);
