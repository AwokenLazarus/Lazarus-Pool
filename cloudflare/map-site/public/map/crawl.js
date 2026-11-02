// The crawl's way out, independent of app.js. app.js wires Engage and Skip only after it has built the
// renderer and the scene, so a browser that cannot run the map (no WebGL: app.js throws at the renderer)
// was left on a crawl whose buttons did nothing and whose error sat behind it (2026-10-29, Discord).
// Classic script, loaded before the module: it runs even when the module never does.
(() => {
  const crawl = document.getElementById("crawl");
  if (!crawl) return;
  const dismiss = () => {
    // app.js running: it ends the crawl itself and starts the intro flight
    if (window.__xbtgEndCrawl) return window.__xbtgEndCrawl();
    if (crawl.classList.contains("gone")) return;
    crawl.classList.add("gone");
    try { localStorage.setItem("xbtg-crawl", "seen"); } catch { /* private mode */ }
    setTimeout(() => crawl.remove(), 1400);
    for (const x of document.body.children) x.removeAttribute("inert");
  };
  const failed = () => {
    dismiss();
    const err = document.getElementById("error");
    if (err && err.hidden) {
      err.textContent = "This map could not start in this browser. The same pools and gateways are in plain tables at /map/table/.";
      err.hidden = false;
    }
    document.getElementById("loading")?.classList.add("done");
  };
  for (const id of ["engage", "skip"]) document.getElementById(id)?.addEventListener("click", () => { if (!window.__xbtgEndCrawl) dismiss(); });
  // An uncaught error from the map's own code or its three.js build means app.js has stopped. So does a
  // script that fails to load (three.js blocked or cut off): that error fires on the <script> element,
  // does not bubble, and is only seen while capturing.
  addEventListener("error", (e) => {
    const t = e.target;
    if (/\/map\//.test(e.filename || "") || (t && t.tagName === "SCRIPT" && /\/map\//.test(t.src || ""))) failed();
  }, true);
  if (!("noModule" in HTMLScriptElement.prototype)) failed(); // no module support: app.js never runs
  setTimeout(dismiss, 50000);
})();
