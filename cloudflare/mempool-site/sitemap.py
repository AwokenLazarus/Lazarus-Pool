#!/usr/bin/env python3
"""Write the explorer's sitemaps: sitemap-en.xml, the pages worth a search result dated by this
build, and sitemap.xml, an index naming it (the same layout as the pool site and the hub).

The list is INDEXABLE in _worker.js, the same list the worker uses to send every other page
X-Robots-Tag: noindex, so the sitemap and what may be indexed cannot drift apart. Adding a page is
one line there (INDEXING.md says what is left out and why).

The explorer is one Angular app, so every page here is the same shell until the browser fills it
in, and the build is when any of them last changed: each <lastmod> is the build time. There is no
<priority>: Google ignores it.

    python3 sitemap.py dist      # build.sh runs this after postprocess.py
"""
import json
import re
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from postprocess import SITE

# The file that lists the pages, and the one robots.txt names. sitemap.xml only points at it.
PAGES = "sitemap-en.xml"


def indexable():
    src = (HERE / "_worker.js").read_text(encoding="utf-8")
    return json.loads(re.search(r"^const INDEXABLE = (\[.*?\]);", src, re.S | re.M).group(1))


def sitemap_xml(built=None):
    """sitemap-en.xml: one <url> per indexable page."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(built))
    urls = "".join(
        f"  <url>\n    <loc>{escape(SITE + path)}</loc>\n    <lastmod>{stamp}</lastmod>\n  </url>\n"
        for path in indexable()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n"
    )


def index_xml():
    """sitemap.xml: an index naming sitemap-en.xml, for crawlers that look at the usual place."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <sitemap><loc>{escape(SITE)}/{PAGES}</loc></sitemap>\n"
        "</sitemapindex>\n"
    )


if __name__ == "__main__":
    out = Path(sys.argv[1])
    (out / PAGES).write_text(sitemap_xml(), encoding="utf-8")
    (out / "sitemap.xml").write_text(index_xml(), encoding="utf-8")
