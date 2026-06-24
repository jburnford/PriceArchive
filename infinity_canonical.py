#!/usr/bin/env python3
"""Infinity block-list result.json -> canonical doc.

Vendored/trimmed from wpcs-ocr/benchmark/ocr_to_canonical.py (Infinity path
only; the chandra/gemini adapters and their ocr_loaders dependency are dropped).
Infinity output is a JSON list of blocks `[{bbox,category,text}]` for a single
page (our PDFs are 1 page each), or a list-of-pages for multi-page docs.
"""
from __future__ import annotations
import json
from pathlib import Path

import canonical as C

# Infinity block category -> canonical vocab
_INF_MAP = {
    "text": "body", "paragraph": "body", "list": "list", "title": "heading",
    "section": "heading", "table": "table", "figure": "figure",
    "figure_caption": "caption", "table_caption": "caption", "caption": "caption",
    "header": "header", "page_header": "header", "footer": "footer",
    "page_footer": "footer", "footnote": "footnote", "page_footnote": "footnote",
    "page_number": "page_number",
}


def blocks_to_canonical(data, doc_id: str, corpus: str = "price") -> dict:
    """Build a canonical doc from already-loaded Infinity JSON (list)."""
    if not isinstance(data, list):
        data = []
    # single flat block list (our case) vs list-of-pages
    if data and isinstance(data[0], dict):
        pages_in = [data]
    else:
        pages_in = [p for p in data if isinstance(p, list)]
    pages = []
    for pi, blocks_in in enumerate(pages_in, 1):
        blocks = []
        for i, b in enumerate(blocks_in):
            if not isinstance(b, dict):
                continue
            cat = _INF_MAP.get((b.get("category") or "").lower(), "body")
            raw = b.get("text", "")
            block = {"order": i, "category": cat,
                     "raw_category": (b.get("category") or "").lower()}
            if cat == "table" and "<" in (raw or ""):
                block["cells"] = C.cells_from_html(raw)
                block["text"] = "\n".join(" ".join(c for c in r if c)
                                          for r in block["cells"])
            else:
                block["text"] = C.clean_markup(raw)
            if b.get("bbox"):
                block["bbox"] = b["bbox"]
            if not block.get("text") and not block.get("cells"):
                continue
            blocks.append(block)
        pages.append({"page": pi, "blocks": blocks})
    return {"doc_id": doc_id, "corpus": corpus,
            "meta": {"tool": "infinity"}, "pages": pages}


def load_canonical(pdf_dir, doc_id: str, corpus: str = "price") -> dict | None:
    """Read <pdf_dir>/result.json and return a canonical doc (None if missing)."""
    jf = Path(pdf_dir) / "result.json"
    if not jf.exists():
        return None
    try:
        data = json.loads(jf.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return blocks_to_canonical(data, doc_id, corpus)
