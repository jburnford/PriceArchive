#!/usr/bin/env python3
"""Phase B2: enrich items with a local LLM (Qwen3-30B-A3B-Instruct, served by
vLLM on Nibi). Heuristic-first hybrid:
  - SUMMARY (+subject) for every substantial item (letters AND documents).
  - METADATA fields (date/place/sender/recipient) are filled ONLY where the
    deterministic pass left them blank (heuristic wins when present).
Guided-JSON decoding keeps output parse-safe. Resumable: skips item_ids already
in the output file. Runs ON Nibi against a local vLLM endpoint.

  python llm_enrich.py items.jsonl out.jsonl --api http://127.0.0.1:PORT/v1 --model qwen3
"""
from __future__ import annotations
import argparse
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "summary": {"type": "string"},
        "date": {"type": ["string", "null"]},
        "place": {"type": ["string", "null"]},
        "sender": {"type": ["string", "null"]},
        "recipient": {"type": ["string", "null"]},
        "doc_subtype": {"type": "string",
                        "enum": ["letter", "circular", "telegram", "legal",
                                 "report", "account", "list", "other"]},
    },
    "required": ["subject", "summary", "doc_subtype"],
}

SYS = ("You are an archivist cataloguing the records of La Compagnie Price, a "
       "19th-20th century Quebec lumber company. The text is OCR of a single "
       "historical document (English or French), possibly imperfect. Return ONLY "
       "JSON matching the schema. The summary is 1-3 sentences, factual, no "
       "speculation. For date use ISO YYYY-MM-DD (or YYYY-MM / YYYY) if a clear "
       "date is present, else null. sender/recipient/place from the document only; "
       "null if not stated. Write the summary in English.")


def call(api, model, text, retries=4):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": text[:8000]}],
        "temperature": 0.0, "max_tokens": 400,
        "guided_json": SCHEMA,
    }).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(api + "/chat/completions", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                out = json.loads(r.read())
            content = out["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:
            if i == retries - 1:
                return {"_error": str(e)[:120]}
            time.sleep(2 * (i + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("items"); ap.add_argument("out")
    ap.add_argument("--api", required=True); ap.add_argument("--model", required=True)
    ap.add_argument("--min-chars", type=int, default=150)
    ap.add_argument("--kinds", default="letter,document,table")
    ap.add_argument("--workers", type=int, default=32)
    a = ap.parse_args()
    kinds = set(a.kinds.split(","))

    items = [json.loads(l) for l in open(a.items)]
    done = set()
    if Path(a.out).exists():
        done = {json.loads(l)["item_id"] for l in open(a.out)}
    todo = [it for it in items if it["kind"] in kinds
            and it["n_chars"] >= a.min_chars and it["item_id"] not in done]
    print(f"[enrich] {len(todo)} items to enrich ({len(done)} already done), "
          f"{a.workers} workers", flush=True)

    lock = threading.Lock()
    n = 0

    def work(it):
        res = call(a.api, a.model, it.get("text", ""))
        return {"item_id": it["item_id"], "kind": it["kind"],
                "subject": res.get("subject"), "summary": res.get("summary"),
                "doc_subtype": res.get("doc_subtype"),
                "llm_date": res.get("date"), "llm_place": res.get("place"),
                "llm_sender": res.get("sender"), "llm_recipient": res.get("recipient"),
                "error": res.get("_error")}

    with open(a.out, "a", encoding="utf-8") as f, \
            ThreadPoolExecutor(max_workers=a.workers) as ex:
        for fut in as_completed([ex.submit(work, it) for it in todo]):
            rec = fut.result()
            with lock:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
                if n % 100 == 0:
                    f.flush()
                    print(f"  ...{n}/{len(todo)}", flush=True)
    print(f"[enrich] done {n}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
