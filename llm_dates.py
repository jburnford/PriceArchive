#!/usr/bin/env python3
"""Dedicated date-extraction pass for sources still missing a date.

Dates are the most valuable access point for the archive, so this targets every
source the heuristic + summary passes left undated (including short dockets /
telegrams the summary pass skipped) with a tight date-only prompt. Concurrent +
resumable. Runs ON Nibi against the vLLM Qwen3 endpoint.

  python llm_dates.py items_refined.jsonl targets.txt dates_llm.jsonl --api URL --model qwen3
"""
from __future__ import annotations
import argparse
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SYS = ("You are an archivist for La Compagnie Price. Extract THE single date of "
       "this one document from its OCR text. Use the document's own date "
       "(dateline, letterhead, or endorsement), NOT dates merely mentioned in the "
       "body. Return ONLY JSON: {\"date\": \"YYYY-MM-DD\" or \"YYYY-MM\" or "
       "\"YYYY\" if a clear date is present, else null}.")


def call(api, model, text, retries=3):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": text[:5000]}],
        "temperature": 0.0, "max_tokens": 40,
        "response_format": {"type": "json_object"},
    }).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(api + "/chat/completions", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                out = json.loads(r.read())
            return json.loads(out["choices"][0]["message"]["content"])
        except Exception as e:
            if i == retries - 1:
                return {"_error": str(e)[:120]}
            time.sleep(1 + i)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("items"); ap.add_argument("targets"); ap.add_argument("out")
    ap.add_argument("--api", required=True); ap.add_argument("--model", required=True)
    ap.add_argument("--workers", type=int, default=48)
    a = ap.parse_args()

    items = {it["item_id"]: it for it in (json.loads(l) for l in open(a.items))}
    targets = [l.strip() for l in open(a.targets) if l.strip()]
    done = set()
    if Path(a.out).exists():
        done = {json.loads(l)["item_id"] for l in open(a.out)}
    todo = [t for t in targets if t in items and t not in done]
    print(f"[dates] {len(todo)} undated sources ({len(done)} done), {a.workers} workers", flush=True)

    lock = threading.Lock(); n = 0
    with open(a.out, "a", encoding="utf-8") as f, \
            ThreadPoolExecutor(max_workers=a.workers) as ex:
        def work(t):
            res = call(a.api, a.model, items[t].get("text", ""))
            return {"item_id": t, "date": res.get("date"), "error": res.get("_error")}
        for fut in as_completed([ex.submit(work, t) for t in todo]):
            rec = fut.result()
            with lock:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
                if n % 200 == 0:
                    f.flush(); print(f"  ...{n}/{len(todo)}", flush=True)
    print(f"[dates] done {n}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
