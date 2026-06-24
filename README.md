# Price Archive (fonds P-666, *La Compagnie Price*)

Indexing and letter-segmentation pipeline for the OCR'd Price Archive — microfilm
**M188** of fonds **P-666** (La Compagnie Price, a Quebec lumber company). 7,130
page-images were OCR'd with Infinity-Parser2-Pro on the Nibi cluster; this repo
turns that per-page JSON into a queryable catalog and best-effort individual letters.

- Corpus: business correspondence, corporate/legal documents, price-current tables,
  French genealogies, a printed finding aid, blank reel frames. ~64% English / ~25%
  French, spanning 1809–1926.
- Reels: `M188-1` (1,996 pp.), `M188-2` (2,759), `M188-3` (2,375).

## Data
Raw OCR (`data/ocr/<name>.pdf/result.json`, Infinity block-list schema
`[{bbox, category, text}]`) is **pulled from Nibi** and gitignored; the canonical
source is `nibi:/project/6080182/infinity/output/price/`. Re-sync with:

```bash
rsync -a --include='*/' --include='result.json' --exclude='*' \
  nibi:/project/6080182/infinity/output/price/ data/ocr/
```

## Pipeline (run from repo root)
| Step | Script | Output |
|------|--------|--------|
| A index | `index_pages.py` | `build/pages.jsonl` — per page: reel, doc_type, language, start-signals |
| B segment | `segment_items.py` (uses `signals.py`) | `build/items.jsonl` + `item_blocks.jsonl` — logical items |
| finding aid | `parse_finding_aid.py` | `build/finding_aid.jsonl` (reference) |
| B2 enrich | `llm_enrich.py` (Qwen3 on Nibi) | per-source summary, subject, metadata → `letters_llm.jsonl` |
| B2b parse | `llm_parse.py` → `apply_llm_parse.py` | split over-merged items at LLM block boundaries + reclassify → `items_refined.jsonl` |
| B2c pages | `llm_pagesum.py` | per-page summary for every non-blank page → `page_llm.jsonl` |
| B2d dates | `llm_dates.py` | dedicated date extraction for undated sources → `dates_llm.jsonl` |
| C catalog | `build_catalog.py` | `build/catalog.duckdb` |
| D verify | `verify_segments.py` | coverage + cleanliness report |

LLM steps run on Nibi via `serve_and_*.slurm` (one Qwen3-30B-A3B-Instruct-2507-FP8
load per job). `canonical.py` / `infinity_canonical.py` are vendored from `wpcs-ocr`.

## Catalog (`build/catalog.duckdb`)
- **`items`** — one row per source (letter/document/table/finding_aid/docket): `kind`,
  `orig_kind`/`kind_src` (provenance), `reel`, `page_start`/`page_end`, `date_iso` +
  `meta_src` (heuristic/llm/llm2), `place`, `addressee`, `signatory`, `subject`,
  `summary`, `doc_subtype`, `confidence`, `review_flag`, `text_content`.
- **`pages`** — one row per page incl. `page_summary`, `page_kind`, `doc_type`, `text_content`.
- **`item_pages`** (item↔page), **`finding_aid`** (reference), **`letters`** view = `items WHERE kind='letter'`.
- Full-text search (FTS) on items + pages.

```sql
-- correspondence in a date window, with summaries
SELECT item_id, date_iso, signatory, addressee, summary
FROM letters WHERE date_iso BETWEEN '1840' AND '1850' ORDER BY date_iso;

-- full-text search across sources
SELECT item_id, kind, date_iso, summary FROM (
  SELECT *, fts_main_items.match_bm25(item_id,'pulp mill') AS s FROM items) t
WHERE s IS NOT NULL ORDER BY s DESC LIMIT 20;

-- page-level browse with per-page summaries
SELECT doc_id, page_kind, page_summary FROM pages
WHERE reel='M188-2' AND doc_type<>'blank' ORDER BY page_num;
```
