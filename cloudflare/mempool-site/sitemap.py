#!/usr/bin/env python3
"""Write the explorer's sitemap.xml: the pages worth a search result, dated by this build.

The explorer is one Angular app, so every page here is the same shell until the browser fills it
in, and the build is when any of them last changed: each <lastmod> is the build time. Addresses,
transactions and the API stay out (robots.txt in _worker.js disallows them; tests/sitemap.py fails
CI if this list names a disallowed path). Adding a page to the sitemap is one line in PAGES.

    python3 sitemap.py dist      # build.sh runs this after postprocess.py
"""
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from postprocess import SITE

# Path and priority, most important first.
PAGES = (
    ("/", 1.0),
    ("/mining", 0.64),
    ("/graphs", 0.64),
    ("/docs", 0.64),
    ("/about", 0.64),
    ("/mempool-block/0", 0.64),
    ("/rbf", 0.64),
    ("/mining/pool/lazarus", 0.64),
    ("/blocks", 0.64),
    ("/docs/api", 0.64),
    ("/docs/faq", 0.64),
    ("/clock/mempool/0", 0.64),
    ("/clock/mined/0", 0.64),
    ("/tools/calculator", 0.64),
    ("/lazarus/NOTICE", 0.64),
    ("/terms-of-service", 0.64),
    ("/privacy-policy", 0.64),
    ("/trademark-policy", 0.64),
    ("/3rdpartylicenses.txt", 0.64),
)


def sitemap_xml(built=None):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(built))
    urls = "".join(
        f"  <url>\n    <loc>{escape(SITE + path)}</loc>\n    <lastmod>{stamp}</lastmod>\n"
        f"    <priority>{prio:.4f}</priority>\n  </url>\n"
        for path, prio in PAGES
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n"
    )


if __name__ == "__main__":
    (Path(sys.argv[1]) / "sitemap.xml").write_text(sitemap_xml(), encoding="utf-8")
