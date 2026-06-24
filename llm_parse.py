#!/usr/bin/env python3
"""Phase B2b: LLM boundary adjudication to improve letter parsing.

For each candidate item (multi-page or review-flagged letters/documents) we send
the NUMBERED reading-order blocks and ask the model to mark the block index where
each distinct letter/document begins. Splitting then happens at those exact block
indices (no fuzzy text matching). Runs ON Nibi against the vLLM Qwen3 endpoint.

  python llm_parse.py items.jsonl item_blocks.jsonl out.jsonl --api URL --model qwen3
"""
from __future__ import annotations
import argparse
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SYS = ("You are an archivist for La Compagnie Price (a Quebec lumber company). "
       "Below are the NUMBERED OCR text blocks of ONE microfilmed item. The item "
       "may hold a SINGLE document or SEVERAL letters/documents filed together. "
       "Identify each distinct letter or document and the block index where it "
       "BEGINS (a new dateline, salutation, letterhead, or title marks a start). "
       "Return ONLY JSON: {\"segments\":[{\"start_block\":int,\"kind\":one of "
       "letter|telegram|circular|legal|report|account|list|table|other,"
       "\"date\":\"YYYY-MM-DD|YYYY-MM|YYYY|null\",\"sender\":str|null,"
       "\"recipient\":str|null,\"subject\":str}]}. The first segment MUST have "
       "start_block 0. If it is a single document, return exactly one segment. "
       "Do not invent boundaries; only split where a new document clearly starts.")


def numbered(blocks, max_blocks=80, max_chars=9000):
    out, tot = [], 0
    for i, b in enumerate(blocks[:max_blocks]):
        t = (b.get("text") or "").replace("\n", " ").strip()
        line = f"[{i}] {t[:220]}"
        tot += len(line)
        out.append(line)
        if tot > max_chars:
            break
    return "\n".join(out)


def call(api, model, prompt, retries=4):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": prompt}],
        "temperature": 0.0, "max_tokens": 700,
        "response_format": {"type": "json_object"},
    }).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(api + "/chat/completions", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                out = json.loads(r.read())
            return json.loads(out["choices"][0]["message"]["content"])
        except Exception as e:
            if i == retries - 1:
                return {"_error": str(e)[:120]}
            time.sleep(2 * (i + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("items"); ap.add_argument("blocks"); ap.add_argument("out")
    ap.add_argument("--api", required=True); ap.add_argument("--model", required=True)
    ap.add_argument("--workers", type=int, default=32)
    a = ap.parse_args()

    items = {it["item_id"]: it for it in (json.loads(l) for l in open(a.items))}
    blocks = {b["item_id"]: b["blocks"] for b in (json.loads(l) for l in open(a.blocks))}
    # candidates: multi-page or review-flagged letters/documents
    cand = [iid for iid, it in items.items()
            if it["kind"] in ("letter", "document")
            and (it.get("review_flag") or it["n_pages"] >= 2)]
    done = set()
    if Path(a.out).exists():
        done = {json.loads(l)["item_id"] for l in open(a.out)}
    cand = [c for c in cand if c not in done]
    print(f"[parse] {len(cand)} candidates ({len(done)} done), {a.workers} workers", flush=True)

    lock = threading.Lock(); n = 0

    def work(iid):
        bl = blocks.get(iid, [])
        res = call(a.api, a.model, numbered(bl))
        segs = res.get("segments") if isinstance(res, dict) else None
        return {"item_id": iid, "n_blocks": len(bl),
                "segments": segs, "error": res.get("_error")}

    with open(a.out, "a", encoding="utf-8") as f, \
            ThreadPoolExecutor(max_workers=a.workers) as ex:
        for fut in as_completed([ex.submit(work, c) for c in cand]):
            rec = fut.result()
            with lock:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
                if n % 100 == 0:
                    f.flush(); print(f"  ...{n}/{len(cand)}", flush=True)
    print(f"[parse] done {n}/{len(cand)}", flush=True)


if __name__ == "__main__":
    main()
