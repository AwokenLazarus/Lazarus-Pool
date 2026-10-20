"""Check the explorer's sitemap against its robots.txt. No network access, no build needed.

Fails when the sitemap names a page twice, a page on another host or one robots.txt disallows,
when sitemap.xml stops being an index of sitemap-en.xml, or when robots.txt stops pointing
crawlers at sitemap-en.xml.
"""
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from postprocess import SITE
from sitemap import PAGES, index_xml, sitemap_xml

ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
index = ET.fromstring(index_xml())
assert index.tag == "{%s}sitemapindex" % ns["s"], index.tag
assert [e.text for e in index.findall("s:sitemap/s:loc", ns)] == [f"{SITE}/{PAGES}"], "sitemap.xml must index sitemap-en.xml"
root = ET.fromstring(sitemap_xml(0))
assert root.tag == "{%s}urlset" % ns["s"], root.tag
urls = root.findall("s:url", ns)
locs = [u.find("s:loc", ns).text for u in urls]
assert locs and len(locs) == len(set(locs)), "duplicate sitemap URLs"
assert locs[0] == SITE + "/", locs[0]
for u, loc in zip(urls, locs):
    assert loc.startswith(SITE + "/"), loc
    assert u.find("s:lastmod", ns).text == "1970-01-01T00:00:00+00:00", loc
    assert u.find("s:priority", ns) is None, f"{loc}: <priority> is ignored by Google, leave it out"

robots = re.search(r"const ROBOTS = `([^`]*)`", (HERE / "_worker.js").read_text(encoding="utf-8")).group(1)
assert f"Sitemap: {SITE}/{PAGES}" in robots.splitlines(), f"robots.txt does not name {PAGES}"
# robots.txt matching: a rule is a path prefix, `*` matches any run of characters.
blocked = [re.compile(re.escape(r[len("Disallow: "):]).replace(r"\*", ".*")) for r in robots.splitlines() if r.startswith("Disallow: ")]
for loc in locs:
    path = urlsplit(loc).path
    assert not any(b.match(path) for b in blocked), f"{path} is in the sitemap but robots.txt disallows it"
print(f"PASS: {len(locs)} explorer sitemap URLs in {PAGES}, none disallowed by robots.txt, which names it; sitemap.xml indexes it")
