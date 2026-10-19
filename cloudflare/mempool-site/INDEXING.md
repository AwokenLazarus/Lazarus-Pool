# What search engines may index on mempool.lazarus-xbt.xyz

There is one rule: **a page is in the sitemap, or it answers `X-Robots-Tag: noindex`.**

The list is `INDEXABLE` in `_worker.js`. `sitemap.py` builds `sitemap.xml` from it, and the worker
adds the noindex header to every other HTML answer. To make a page indexable, add it there, run
`python3 tests/sitemap.py` and `node tests/noindex.mjs`, and deploy (`deploy.sh mempool`).

## Indexable (in the sitemap), 16 pages

`/`, `/mining`, `/graphs/mempool`, `/about`, `/mempool-block/0`, `/rbf`, `/mining/pool/lazarus`,
`/blocks/1`, `/docs/api/rest`, `/docs/faq`, `/clock/mempool/0`, `/clock/mined/0`,
`/tools/calculator`, `/terms-of-service`, `/privacy-policy`, `/trademark-policy`

## Noindex, and why (decided 19 Oct 2026)

| What | Why it is left out |
|---|---|
| `/graphs`, `/docs`, `/docs/api`, `/blocks` | The app redirects them to `/graphs/mempool`, `/docs/faq`, `/docs/api/rest` and `/blocks/1`, which are listed. |
| Chart pages: `/graphs/mining/pools`, `/graphs/mining/hashrate-difficulty`, `/graphs/mining/pools-dominance`, `/graphs/mining/block-fees`, `/graphs/mining/block-fees-subsidy`, `/graphs/mining/block-rewards`, `/graphs/mining/block-fee-rates`, `/graphs/mining/block-sizes-weights`, `/graphs/price` | Rendered in the browser, they are a chart plus axis labels and range buttons, with no prose (checked 19 Oct). Too thin for search for now. Add them to `INDEXABLE` if they get real text. |
| `/docs/api/websocket`, `/blocks/stale`, other `/blocks/N` and `/mempool-block/N`, other `/clock/...` and `/tools/...` pages | Variants of listed pages, or thin. |
| Other pools' pages, `/mining/pool/<slug>` (except `lazarus`) | Thin, and about other pools. |
| Language copies, `/<lang>/...` (`/de/mining`, `/zh/`, …) | Duplicates: the same unprefixed URL already serves each visitor's language. |
| `/api`, `/api/` (the API docs shell) | The listed API docs page is `/docs/api/rest`. |
| `/address/...`, `/tx/...`, `/block/...` | Auto-generated, one per address, transaction or block. `/address/` and `/tx/` are also disallowed in robots.txt, which stops Google from seeing the noindex. Lifting that Disallow is sequenced in GitHub issue #46 (Mike's call) and must come after this header is live. |
| `/lazarus/NOTICE`, `/lazarus/LICENSE`, `/lazarus/COPYING.md` (`_headers`), `/3rdpartylicenses.txt` (worker) | Licence files the footer links to, not pages. |

Scripts, styles and images carry no robots header: crawlers fetch them to render the pages above.

## Temporary state (19 Oct 2026, until this is deployed)

The explorer is not in the merge-to-main autodeploy. Until `deploy.sh mempool` ships this
directory, a separate bridge Worker, `lazarus-mempool-seo` on Mike's account, answers
`/robots.txt` and `/sitemap.xml` and adds the noindex header to the pages under its routes
(`/graphs*`, `/docs*`, `/blocks*`, `/mining*`, `/tools*`, `/clock*`, `/mempool-block*`, the licence
files). It is generated from this directory's `_worker.js` and `sitemap.py`. Delete it right
**after** the Pages deploy, not before: `wrangler delete --name lazarus-mempool-seo`.
