// Check which explorer answers carry X-Robots-Tag: noindex, against a stand-in for the Pages
// assets. No network: the sitemap pages must stay indexable, every other page and text file must
// not.
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const src = new URL("../_worker.js", import.meta.url);
const dir = mkdtempSync(join(tmpdir(), "mempool-worker-"));
writeFileSync(join(dir, "worker.mjs"), readFileSync(src));
const worker = (await import(join(dir, "worker.mjs"))).default;
const indexable = JSON.parse(/^const INDEXABLE = (\[[\s\S]*?\]);/m.exec(readFileSync(src, "utf8"))[1]);

const FILES = { "/3rdpartylicenses.txt": "text/plain", "/main.0123456789abcdef.js": "text/javascript" };
const env = {
  ASSETS: {
    async fetch(req) {
      const path = new URL(req.url).pathname;
      if (FILES[path]) return new Response("x", { headers: { "Content-Type": FILES[path] } });
      // Pages' SPA fallback: anything else is the app shell, which /<lang>/ also is.
      return new Response("<html></html>", { headers: { "Content-Type": "text/html" } });
    },
  },
};

async function robots(path) {
  const res = await worker.fetch(new Request("https://mempool.lazarus-xbt.xyz" + path), env, {});
  return res.headers.get("X-Robots-Tag");
}

let bad = 0;
const expect = (path, want, got) => {
  if (got !== want) {
    bad++;
    console.error(`${path}: X-Robots-Tag ${got}, want ${want}`);
  }
};
for (const p of indexable) expect(p, null, await robots(p));
for (const p of ["/graphs", "/docs", "/blocks", "/graphs/mining/pools", "/mining/pool/convoy", "/de/mining", "/zh/", "/block/abc", "/address/bc1qtest", "/tx/abc", "/api", "/3rdpartylicenses.txt"]) {
  expect(p, "noindex", await robots(p));
}
expect("/main.0123456789abcdef.js", null, await robots("/main.0123456789abcdef.js"));
if (bad) process.exit(1);
console.log(`PASS: ${indexable.length} sitemap pages indexable; other pages, language copies and text files noindex`);
