#!/usr/bin/env python3
"""Phase C: build build/catalog.duckdb from pages.jsonl + items.jsonl.

Tables: pages (one row/page), items (one row/logical item: letter|document|
table|finding_aid|docket), item_pages (item<->page), finding_aid (reference).
A `letters` VIEW exposes items WHERE kind='letter'. Full-text search via the
DuckDB FTS extension when available, else a lower(text) LIKE fallback.
Atomic write: build into a tempfile, then os.replace over the real catalog.
"""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
OUT = BUILD / "catalog.duckdb"
NIBI = "/project/6080182/infinity/output/price"


def _load(name):
    p = BUILD / name
    return [json.loads(l) for l in p.open()] if p.exists() else []


def main():
    pages = _load("pages.jsonl")
    # prefer the LLM-refined item set (split + reclassified) when present
    items = _load("items_refined.jsonl") or _load("items.jsonl")
    fa = _load("finding_aid.jsonl")
    llm = {r["item_id"]: r for r in _load("letters_llm.jsonl")}
    psum = {r["doc_id"]: r for r in _load("page_llm.jsonl")}
    BUILD.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".duckdb", dir=str(BUILD))
    os.close(fd)
    os.remove(tmp)                      # duckdb wants to create it
    con = duckdb.connect(tmp)

    con.execute("""CREATE TABLE pages(
        doc_id TEXT PRIMARY KEY, reel TEXT, page_num INTEGER, n_blocks INTEGER,
        n_chars BIGINT, language TEXT, doc_type TEXT, failure_label TEXT,
        page_summary TEXT, page_kind TEXT,
        text_content TEXT, ocr_json_path TEXT)""")
    con.executemany("INSERT INTO pages VALUES (" + ",".join("?" * 12) + ")", [
        (p["doc_id"], p["reel"], p["page_num"], p["n_blocks"], p["n_chars"],
         p["language"], p["doc_type"], p["failure_label"],
         psum.get(p["doc_id"], {}).get("page_summary"),
         psum.get(p["doc_id"], {}).get("page_kind"),
         "\n".join(b["text"] for b in p["blocks"]),
         f"{NIBI}/{p['doc_id']}.pdf/result.json") for p in pages])

    con.execute("""CREATE TABLE items(
        item_id TEXT PRIMARY KEY, kind TEXT, orig_kind TEXT, kind_src TEXT,
        reel TEXT, page_start INTEGER,
        page_end INTEGER, n_pages INTEGER, n_chars BIGINT, language TEXT,
        date_iso TEXT, date_raw TEXT, date_precision TEXT, place TEXT,
        addressee TEXT, signatory TEXT, title TEXT, no_marker INTEGER,
        confidence DOUBLE, review_flag BOOLEAN, review_reasons TEXT,
        subject TEXT, summary TEXT, doc_subtype TEXT, meta_src TEXT,
        llm_enriched BOOLEAN, text_content TEXT)""")

    # LLM-identified correspondence subtypes -> promote document items to letters
    CORRESP = {"letter", "circular", "telegram", "memo", "memorandum", "note"}

    def row(it):
        e = llm.get(it["item_id"], {})
        place = it.get("place") or e.get("llm_place")
        addressee = it.get("addressee") or e.get("llm_recipient")
        signatory = it.get("signatory") or e.get("llm_sender")
        date_iso = it.get("date_iso") or e.get("llm_date")
        src = "date:h" if it.get("date_iso") else ("date:llm" if e.get("llm_date") else "")
        kind, kind_src = it["kind"], "heuristic"
        if it["kind"] == "document" and (e.get("doc_subtype") or "").lower() in CORRESP:
            kind, kind_src = "letter", "llm-reclass"
        return (it["item_id"], kind, it["kind"], kind_src, it["reel"],
                it["page_start"], it["page_end"],
                it["n_pages"], it["n_chars"], it["language"], date_iso,
                it.get("date_raw"), it.get("date_precision"), place,
                addressee, signatory, it.get("title"), it.get("no_marker"),
                it.get("confidence"), it.get("review_flag", False),
                ", ".join(it.get("review_reasons", []) or []),
                e.get("subject"), e.get("summary"), e.get("doc_subtype"),
                src, bool(e), it.get("text", ""))
    con.executemany("INSERT INTO items VALUES (" + ",".join("?" * 27) + ")",
                    [row(it) for it in items])

    con.execute("CREATE TABLE item_pages(item_id TEXT, doc_id TEXT)")
    con.executemany("INSERT INTO item_pages VALUES (?,?)",
                    [(it["item_id"], d) for it in items for d in it["doc_ids"]])

    con.execute("""CREATE TABLE finding_aid(
        series TEXT, sous_serie TEXT, title TEXT, date_start INTEGER,
        date_end INTEGER, article_lo INTEGER, article_hi INTEGER,
        description TEXT, source_page TEXT)""")
    if fa:
        con.executemany("INSERT INTO finding_aid VALUES (?,?,?,?,?,?,?,?,?)", [
            (r.get("series"), r.get("sous_serie"), r.get("title"),
             r.get("date_start"), r.get("date_end"), r.get("article_lo"),
             r.get("article_hi"), r.get("description"),
             r.get("source_page")) for r in fa])

    con.execute("CREATE VIEW letters AS SELECT * FROM items WHERE kind='letter'")

    fts = False
    try:
        con.execute("INSTALL fts; LOAD fts;")
        con.execute("PRAGMA create_fts_index('items','item_id','text_content','title','addressee','signatory','subject','summary', overwrite=1)")
        con.execute("PRAGMA create_fts_index('pages','doc_id','text_content','page_summary', overwrite=1)")
        fts = True
    except Exception as e:
        print("[catalog] FTS unavailable, LIKE fallback only:", str(e)[:80])

    con.close()
    os.replace(tmp, OUT)
    print(f"[catalog] wrote {OUT}")
    print(f"[catalog] pages={len(pages)} items={len(items)} finding_aid={len(fa)} fts={fts}")


if __name__ == "__main__":
    main()
