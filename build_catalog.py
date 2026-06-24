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
    items = _load("items.jsonl")
    fa = _load("finding_aid.jsonl")
    BUILD.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".duckdb", dir=str(BUILD))
    os.close(fd)
    os.remove(tmp)                      # duckdb wants to create it
    con = duckdb.connect(tmp)

    con.execute("""CREATE TABLE pages(
        doc_id TEXT PRIMARY KEY, reel TEXT, page_num INTEGER, n_blocks INTEGER,
        n_chars BIGINT, language TEXT, doc_type TEXT, failure_label TEXT,
        text_content TEXT, ocr_json_path TEXT)""")
    con.executemany("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (p["doc_id"], p["reel"], p["page_num"], p["n_blocks"], p["n_chars"],
         p["language"], p["doc_type"], p["failure_label"],
         "\n".join(b["text"] for b in p["blocks"]),
         f"{NIBI}/{p['doc_id']}.pdf/result.json") for p in pages])

    con.execute("""CREATE TABLE items(
        item_id TEXT PRIMARY KEY, kind TEXT, reel TEXT, page_start INTEGER,
        page_end INTEGER, n_pages INTEGER, n_chars BIGINT, language TEXT,
        date_iso TEXT, date_raw TEXT, date_precision TEXT, place TEXT,
        addressee TEXT, signatory TEXT, title TEXT, no_marker INTEGER,
        confidence DOUBLE, review_flag BOOLEAN, review_reasons TEXT,
        text_content TEXT)""")
    con.executemany("INSERT INTO items VALUES (" + ",".join("?" * 20) + ")", [
        (it["item_id"], it["kind"], it["reel"], it["page_start"], it["page_end"],
         it["n_pages"], it["n_chars"], it["language"], it.get("date_iso"),
         it.get("date_raw"), it.get("date_precision"), it.get("place"),
         it.get("addressee"), it.get("signatory"), it.get("title"),
         it.get("no_marker"), it.get("confidence"), it.get("review_flag", False),
         ", ".join(it.get("review_reasons", []) or []), it.get("text", ""))
        for it in items])

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
        con.execute("PRAGMA create_fts_index('items','item_id','text_content','title','addressee','signatory', overwrite=1)")
        con.execute("PRAGMA create_fts_index('pages','doc_id','text_content', overwrite=1)")
        fts = True
    except Exception as e:
        print("[catalog] FTS unavailable, LIKE fallback only:", str(e)[:80])

    con.close()
    os.replace(tmp, OUT)
    print(f"[catalog] wrote {OUT}")
    print(f"[catalog] pages={len(pages)} items={len(items)} finding_aid={len(fa)} fts={fts}")


if __name__ == "__main__":
    main()
