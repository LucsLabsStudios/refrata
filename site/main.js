// Latest release from GitHub → download links, version, size and SHA-256 on the page.
const REPO = "LucsLabsStudios/refrata";

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
      document.querySelectorAll("[data-size]").forEach((el) => (el.textContent = `versão ${version} · ${mb} MB`));
      const sha = String(setup.digest || "").replace(/^sha256:/i, "");
      if (sha) {
        document.querySelectorAll("[data-sha]").forEach((el) => {
          el.innerHTML = ` · SHA-256 <code></code>`;
          el.querySelector("code").textContent = sha;
        });
      }
    }
    if (zip) document.querySelectorAll("[data-portable]").forEach((a) => (a.href = zip.browser_download_url));
    if (version) {
      document.querySelectorAll("[data-version]").forEach((el) => (el.textContent = version));
      document.querySelectorAll("[data-version-line]").forEach((el) => (el.textContent = `Versão ${version} · Windows 10 e 11`));
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
  ".section > .eyebrow, .section > h2, .grid > *, .steps > *, .policy > *, .music-copy > *, .music-visual, .split-copy > *, .split-shot, .duo > *, .download, .formats");
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
const win = document.querySelector(".hero-shot .window");
const tilt = document.querySelectorAll(".hero-shot .window");
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
  hero.addEventListener("pointermove", (e) => {
    const r = win.getBoundingClientRect();
    const nx = (e.clientX - (r.left + r.width / 2)) / window.innerWidth;
    const ny = (e.clientY - (r.top + r.height / 2)) / window.innerHeight;
    target = { x: rest.x - ny * 8, y: rest.y + nx * 14 };
    kick();
  });
  hero.addEventListener("pointerleave", () => { target = { ...rest }; kick(); });
}

// card spotlight follows the cursor
if (finePointer) {
  document.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${e.clientX - r.left}px`);
      card.style.setProperty("--my", `${e.clientY - r.top}px`);
    });
  });
}
