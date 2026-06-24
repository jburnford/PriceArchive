#!/usr/bin/env python3
"""Phase A: index every OCR page into build/pages.jsonl (+ pages_bbox.jsonl).

One JSONL record per page: reel, page_num, n_blocks, n_chars, language (en/fr
heuristic), doc_type (blank|finding_aid|table|docket|letter|other), the
reading-order blocks, and the detected start_signals (so Phase B needn't
recompute). A parallel pages_bbox.jsonl keeps bboxes for the Phase D overlay.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

import infinity_canonical as IC
import signals as S

ROOT = Path(__file__).resolve().parent
OCR = ROOT / "data" / "ocr"
BUILD = ROOT / "build"
REEL_RE = re.compile(r"^(M188-\d)_(\d+)$")

# tiny language-ID lexicons (accent-stripped, lowercased)
_FR = {"le", "la", "les", "des", "du", "de", "et", "est", "une", "un", "dans",
       "pour", "avec", "sur", "cette", "qui", "que", "ne", "pas", "vous", "nous",
       "votre", "notre", "monsieur", "messieurs", "recu", "janvier", "fevrier",
       "mars", "avril", "juin", "juillet", "aout", "septembre", "octobre",
       "novembre", "decembre", "compagnie", "lettre", "annee"}
_EN = {"the", "and", "dear", "sir", "yours", "your", "you", "we", "our", "this",
       "that", "with", "for", "have", "not", "are", "was", "will", "company",
       "letter", "received", "of", "to", "in", "is", "be", "as", "shall"}
_WORD = re.compile(r"[a-z]+")


def language(text: str) -> tuple[str, dict]:
    toks = _WORD.findall(S.strip_accents(text).lower())
    if not toks:
        return "en", {"en": 0, "fr": 0}
    fr = sum(t in _FR for t in toks)
    en = sum(t in _EN for t in toks)
    lang = "fr" if fr > en and fr > 0 else "en"
    n = len(toks)
    return lang, {"en": round(en / n, 3), "fr": round(fr / n, 3)}


def classify(reel, page_num, blocks, n_chars, sigs) -> str:
    n_blocks = len(blocks)
    if n_blocks == 0 or n_chars < 50:
        return "blank"
    if reel == "M188-1" and 1 <= page_num <= 19:
        return "finding_aid"
    if any(b.get("category") == "table" or b.get("raw_category") == "table"
           for b in blocks):
        return "table"
    has_start = any(s["type"] in ("place_date", "salutation") for s in sigs)
    if has_start:
        return "letter"
    if n_chars < 150:
        return "docket"
    return "other"


def main():
    BUILD.mkdir(exist_ok=True)
    dirs = sorted(OCR.glob("M188-*.pdf"))
    print(f"[index] {len(dirs)} page dirs")
    fp = (BUILD / "pages.jsonl").open("w", encoding="utf-8")
    fb = (BUILD / "pages_bbox.jsonl").open("w", encoding="utf-8")
    counts = {}
    n = 0
    for d in dirs:
        doc_id = d.name[:-4] if d.name.endswith(".pdf") else d.name
        m = REEL_RE.match(doc_id)
        if not m:
            continue
        reel, page_num = m.group(1), int(m.group(2))
        doc = IC.load_canonical(d, doc_id)
        blocks = doc["pages"][0]["blocks"] if doc and doc["pages"] else []
        text = "\n".join(b.get("text", "") for b in blocks)
        n_chars = len(text)
        lang, lang_scores = language(text)
        sigs = S.page_start_signals(blocks)
        dtype = classify(reel, page_num, blocks, n_chars, sigs)
        # minimal OCR-quality flag (loops already repaired on Nibi; catch stragglers)
        failure = "long_block" if any(len(b.get("text", "")) > 15000 for b in blocks) else "ok"
        counts[dtype] = counts.get(dtype, 0) + 1

        slim = [{"order": b["order"], "category": b["category"], "text": b.get("text", "")}
                for b in blocks]
        rec = {"doc_id": doc_id, "reel": reel, "page_num": page_num,
               "filename": doc_id, "n_blocks": len(blocks), "n_chars": n_chars,
               "language": lang, "lang_scores": lang_scores, "doc_type": dtype,
               "failure_label": failure, "start_signals": sigs, "blocks": slim}
        fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        bb = [{"order": b["order"], "category": b["category"], "bbox": b.get("bbox")}
              for b in blocks]
        fb.write(json.dumps({"doc_id": doc_id, "blocks": bb}, ensure_ascii=False) + "\n")
        n += 1
    fp.close()
    fb.close()
    print(f"[index] wrote {n} pages -> build/pages.jsonl")
    print("[index] doc_type:", dict(sorted(counts.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
