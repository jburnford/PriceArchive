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
| Phase | Script | Output |
|------|--------|--------|
| A | `index_pages.py` | `build/pages.jsonl` (+ `pages_bbox.jsonl`) — one record per page |
| B | `segment_letters.py` (uses `signals.py`) | `build/letters.jsonl` + `build/letters/<id>/` |
| B2 | `llm_enrich.py` | Qwen3-MoE on Nibi: edge-case repair + per-letter summaries |
| — | `parse_finding_aid.py` | `build/finding_aid.jsonl` (reference only) |
| C | `build_catalog.py` | `build/catalog.duckdb` (`pages`, `letters`, `letter_pages`, `finding_aid`) |
| D | `verify_segments.py` | distributions + QA contact sheet |

`canonical.py` and `infinity_canonical.py` are vendored from the `wpcs-ocr`
benchmark repo so this repo is self-contained.

## Catalog
DuckDB with full-text search. Example:
```sql
SELECT letter_id, date_iso, sender, recipient, summary
FROM letters WHERE date_iso >= '1880' ORDER BY date_iso;
```
