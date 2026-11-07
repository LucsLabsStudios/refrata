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

// gentle reveal of sections while scrolling
const io = "IntersectionObserver" in window
  ? new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }), { threshold: 0.12 })
  : null;
document.querySelectorAll(".section > *, .hero-shot").forEach((el) => {
  if (!io) return;
  el.classList.add("reveal");
  io.observe(el);
});
