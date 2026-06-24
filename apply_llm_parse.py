#!/usr/bin/env python3
"""Apply LLM boundary adjudication: split over-merged items and reclassify.

Reads items.jsonl, item_blocks.jsonl, llm_parse.jsonl. For each analysed item:
  - >=2 segments  -> split its blocks at the LLM start_block indices into
    sub-items, re-deriving reel/pages/doc_ids/text and taking date/sender/
    recipient/subject/kind from the segment.
  - 1 segment     -> keep, but adopt the segment kind (correspondence -> letter).
Items with no analysis pass through unchanged. Writes items_refined.jsonl and
item_blocks_refined.jsonl (so summaries can be regenerated for new sub-items).
"""
from __future__ import annotations
import json
import re
from pathlib import Path

BUILD = Path(__file__).resolve().parent / "build"
CORRESP = {"letter", "telegram", "circular", "memo", "memorandum", "note"}
DOCID = re.compile(r"^(M188-\d)_(\d+)$")


def kind_of(seg_kind, fallback):
    k = (seg_kind or "").lower()
    if k in CORRESP:
        return "letter"
    if k == "table":
        return "table"
    if k in ("legal", "report", "account", "list", "other", "contract",
             "resolution", "minutes", "petition", "affidavit"):
        return "document"
    return fallback


def pages_of(blocks):
    seen = []
    for b in blocks:
        if b["doc_id"] not in seen:
            seen.append(b["doc_id"])
    nums = [int(DOCID.match(d).group(2)) for d in seen if DOCID.match(d)]
    reel = DOCID.match(seen[0]).group(1) if seen and DOCID.match(seen[0]) else None
    return seen, reel, (min(nums) if nums else 0), (max(nums) if nums else 0)


def sanitize(segments, n):
    pts = sorted({max(0, min(int(s.get("start_block", 0)), n - 1)) for s in segments
                  if isinstance(s, dict) and s.get("start_block") is not None})
    if not pts or pts[0] != 0:
        pts = [0] + [p for p in pts if p != 0]
    # map each start point back to its segment dict
    by_start = {}
    for s in segments:
        if isinstance(s, dict) and s.get("start_block") is not None:
            by_start[max(0, min(int(s["start_block"]), n - 1))] = s
    return pts, by_start


def main():
    items = {it["item_id"]: it for it in (json.loads(l) for l in open(BUILD / "items.jsonl"))}
    blocks = {b["item_id"]: b["blocks"] for b in (json.loads(l) for l in open(BUILD / "item_blocks.jsonl"))}
    parse = {}
    pf = BUILD / "llm_parse.jsonl"
    if pf.exists():
        parse = {r["item_id"]: r for r in (json.loads(l) for l in open(pf))
                 if r.get("segments")}

    out_items, out_blocks = [], []
    n_split = n_reclass = 0
    for iid, it in items.items():
        bl = blocks.get(iid, [])
        pr = parse.get(iid)
        segs = pr["segments"] if pr else None
        if not segs or not bl:
            out_items.append(it); out_blocks.append({"item_id": iid, "blocks": bl})
            continue
        pts, by_start = sanitize(segs, len(bl))
        if len(pts) <= 1:
            seg = by_start.get(0, {})
            nk = kind_of(seg.get("kind"), it["kind"])
            if nk != it["kind"]:
                it = {**it, "kind": nk, "orig_kind": it["kind"], "kind_src": "llm-parse"}
                n_reclass += 1
            out_items.append(it); out_blocks.append({"item_id": iid, "blocks": bl})
            continue
        # split
        n_split += 1
        for si, lo in enumerate(pts):
            hi = pts[si + 1] if si + 1 < len(pts) else len(bl)
            sub_bl = bl[lo:hi]
            if not sub_bl:
                continue
            seg = by_start.get(lo, {})
            doc_ids, reel, ps, pe = pages_of(sub_bl)
            text = "\n".join(b["text"] for b in sub_bl if b.get("text"))
            sid = iid if si == 0 else f"{iid}_s{si}"
            out_items.append({
                "item_id": sid, "kind": kind_of(seg.get("kind"), it["kind"]),
                "orig_kind": it["kind"], "kind_src": "llm-split",
                "reel": reel or it["reel"], "page_start": ps, "page_end": pe,
                "n_pages": len(doc_ids), "doc_ids": doc_ids, "n_chars": len(text),
                "language": it["language"],
                "date_iso": seg.get("date"), "date_raw": seg.get("date"),
                "place": None, "addressee": seg.get("recipient"),
                "signatory": seg.get("sender"), "title": seg.get("subject"),
                "confidence": 0.5, "review_flag": True,
                "review_reasons": ["llm_split"], "text": text})
            out_blocks.append({"item_id": sid, "blocks": sub_bl})

    with (BUILD / "items_refined.jsonl").open("w", encoding="utf-8") as fi, \
            (BUILD / "item_blocks_refined.jsonl").open("w", encoding="utf-8") as fb:
        for it in out_items:
            fi.write(json.dumps(it, ensure_ascii=False) + "\n")
        for b in out_blocks:
            fb.write(json.dumps(b, ensure_ascii=False) + "\n")
    print(f"[apply] {len(items)} items -> {len(out_items)} refined "
          f"({n_split} split, {n_reclass} reclassified by parse)")


if __name__ == "__main__":
    main()
