# What search engines may index on mempool.lazarus-xbt.xyz

There is one rule: **a page is in the sitemap, or it answers `X-Robots-Tag: noindex`.**

The list is `INDEXABLE` in `_worker.js`. `sitemap.py` builds `sitemap-en.xml` from it, plus
`sitemap.xml`, an index that names `sitemap-en.xml` (the layout the pool site and the hub use).
robots.txt names `sitemap-en.xml`. The worker adds the noindex header to every other HTML answer.
To make a page indexable, add it there, run `python3 tests/sitemap.py` and `node tests/noindex.mjs`,
and deploy (`deploy.sh mempool`).

## Indexable (in the sitemap), 5 pages

`/`, `/mining`, `/mining/pool/lazarus`, `/blocks/1`, `/graphs/mempool`

These are the pages about this chain and this pool. `/blocks/1` and `/graphs/mempool` are where the
app sends `/blocks` and `/graphs`, so the sitemap names the page a crawler lands on. The entries
carry `<lastmod>` (the build time) and no `<priority>`, which Google ignores.

## Noindex, and why (19 Oct 2026; trimmed to pool pages the same day, on IT's advice)

| What | Why it is left out |
|---|---|
| `/about`, `/docs/faq`, `/docs/api/rest`, `/docs/api/websocket`, `/terms-of-service`, `/privacy-policy`, `/trademark-policy`, `/tools/calculator` | Stock mempool.space pages, word for word (`/about` gains one attribution paragraph from the theme). A copy of another site's page earns no result of its own. |
| `/clock/...`, `/mempool-block/N`, `/rbf` | Live widgets: numbers that change by the second, no text to rank. |
| `/graphs`, `/docs`, `/docs/api`, `/blocks` | The app redirects them to `/graphs/mempool`, `/docs/faq`, `/docs/api/rest` and `/blocks/1`. |
| Chart pages: `/graphs/mining/pools`, `/graphs/mining/hashrate-difficulty`, `/graphs/mining/pools-dominance`, `/graphs/mining/block-fees`, `/graphs/mining/block-fees-subsidy`, `/graphs/mining/block-rewards`, `/graphs/mining/block-fee-rates`, `/graphs/mining/block-sizes-weights`, `/graphs/price` | Rendered in the browser, they are a chart plus axis labels and range buttons, with no prose (checked 19 Oct). Too thin for search for now. Add them to `INDEXABLE` if they get real text. |
| `/blocks/stale`, other `/blocks/N` | Variants of a listed page. |
| Other pools' pages, `/mining/pool/<slug>` (except `lazarus`) | Thin, and about other pools. |
| Language copies, `/<lang>/...` (`/de/mining`, `/zh/`, …) | Duplicates: the same unprefixed URL already serves each visitor's language. |
| `/api`, `/api/` (the API docs shell) | Stock API docs, as above. |
| `/address/...`, `/tx/...`, `/block/...` | Auto-generated, one per address, transaction or block. `/address/` and `/tx/` are also disallowed in robots.txt, which stops Google from seeing the noindex. Lifting that Disallow is sequenced in GitHub issue #46 (Mike's call) and must come after this header is live. |
| `/lazarus/NOTICE`, `/lazarus/LICENSE`, `/lazarus/COPYING.md` (`_headers`), `/3rdpartylicenses.txt` (worker) | Licence files the footer links to, not pages. |

Scripts, styles and images carry no robots header: crawlers fetch them to render the pages above.

## robots.txt

Unchanged apart from the `Sitemap:` line: `Allow: /`, then Disallow `/address/`, `/tx/`, `/api/`
and their language-prefixed forms (`/*/address/`, `/*/tx/`). Nothing in the sitemap may match a
Disallow; `tests/sitemap.py` checks it.

## Temporary state (19 Oct 2026, until this is deployed)

The explorer is not in the merge-to-main autodeploy. Until `deploy.sh mempool` ships this
directory, a separate bridge Worker, `lazarus-mempool-seo` on Mike's account, answers
`/robots.txt`, `/sitemap.xml` and `/sitemap-en.xml` and adds the noindex header to the pages under
its routes (`/graphs*`, `/docs*`, `/blocks*`, `/mining*`, `/tools*`, `/clock*`, `/mempool-block*`,
`/about*`, `/rbf*`, `/terms-of-service*`, `/privacy-policy*`, `/trademark-policy*`, the licence
files). It is generated from this directory's `_worker.js` and `sitemap.py`. Delete it right
**after** the Pages deploy, not before: `wrangler delete --name lazarus-mempool-seo`.
