#!/usr/bin/env python3
"""Parse the printed P-666 finding aid (M188-1 pp.1-19) into a reference table.

Reference only — it is NOT used to anchor image numbers to items (the OCR didn't
capture a reliable image->item map). Captures the series / sous-série hierarchy
and each described entry with its date span and any [article range].
Reads build/pages.jsonl, writes build/finding_aid.jsonl.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

BUILD = Path(__file__).resolve().parent / "build"

_SERIE = re.compile(r"^\s*SERIE\s+(\d+)\s*:\s*(.+)$", re.I)
_SOUS = re.compile(r"^\s*SOUS-?SERIE\s+([\d, ]+?)\s*:\s*(.+)$", re.I)
_SUBSEC = re.compile(r"^\s*(\d+\s*,\s*\d+\s*,\s*\d+)\s*:\s*(.+)$")
_YEAR = re.compile(r"\b(18\d\d|19[0-6]\d)\b")
_ART = re.compile(r"\[\s*(\d+)\s*-\s*(\d+)\s*\]")


def main():
    pages = [json.loads(l) for l in (BUILD / "pages.jsonl").open()]
    fa_pages = sorted((p for p in pages
                       if p["reel"] == "M188-1" and p["page_num"] <= 19),
                      key=lambda p: p["page_num"])
    recs = []
    series = sous = None
    for p in fa_pages:
        for b in p["blocks"]:
            t = (b.get("text") or "").strip()
            if not t:
                continue
            m = _SERIE.match(t)
            if m:
                series = f"{m.group(1)}: {m.group(2).strip()}"
                continue
            m = _SOUS.match(t) or _SUBSEC.match(t)
            if m:
                sous = f"{m.group(1).strip()}: {m.group(2).strip()}"
                continue
            # a described entry: has a date span and/or article range
            yrs = _YEAR.findall(t)
            art = _ART.search(t)
            if yrs or art:
                recs.append({
                    "series": series, "sous_serie": sous,
                    "title": t[:200],
                    "date_start": int(min(yrs)) if yrs else None,
                    "date_end": int(max(yrs)) if yrs else None,
                    "article_lo": int(art.group(1)) if art else None,
                    "article_hi": int(art.group(2)) if art else None,
                    "description": t,
                    "source_page": p["doc_id"],
                })
    with (BUILD / "finding_aid.jsonl").open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[finding_aid] {len(recs)} entries -> build/finding_aid.jsonl")
    for r in recs[:6]:
        print(f"   [{r['series']} | {r['sous_serie']}] {r['date_start']}-{r['date_end']} "
              f"{('art '+str(r['article_lo'])+'-'+str(r['article_hi'])) if r['article_lo'] else ''}"
              f" :: {r['title'][:70]}")


if __name__ == "__main__":
    main()
