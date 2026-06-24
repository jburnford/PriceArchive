#!/usr/bin/env python3
"""Per-page summaries: one short summary for every non-blank page.

Reads pages.jsonl, sends each page's reading-order text to Qwen3, writes
page_llm.jsonl {doc_id, page_summary, page_kind}. Blank pages are recorded
without an LLM call. Concurrent + resumable. Runs ON Nibi against the vLLM
endpoint.

  python llm_pagesum.py pages.jsonl page_llm.jsonl --api URL --model qwen3
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
       "The text is OCR of ONE microfilm page, which may be a whole document or "
       "part of a larger letter/document. Return ONLY JSON: "
       "{\"page_summary\": one factual sentence describing what is on this page, "
       "in English; \"page_kind\": one of letter|document|table|account|list|"
       "telegram|legal|cover|continuation|other}. No speculation.")


def call(api, model, text, retries=3):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": SYS},
                     {"role": "user", "content": text[:6000]}],
        "temperature": 0.0, "max_tokens": 160,
        "response_format": {"type": "json_object"},
    }).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(api + "/chat/completions", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                out = json.loads(r.read())
            return json.loads(out["choices"][0]["message"]["content"])
        except Exception as e:
            if i == retries - 1:
                return {"_error": str(e)[:120]}
            time.sleep(1 + i)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pages"); ap.add_argument("out")
    ap.add_argument("--api", required=True); ap.add_argument("--model", required=True)
    ap.add_argument("--workers", type=int, default=48)
    a = ap.parse_args()

    pages = [json.loads(l) for l in open(a.pages)]
    done = set()
    if Path(a.out).exists():
        done = {json.loads(l)["doc_id"] for l in open(a.out)}

    lock = threading.Lock(); n = 0
    out = open(a.out, "a", encoding="utf-8")

    # blanks: no LLM call
    blanks = [p for p in pages if p["doc_type"] == "blank" and p["doc_id"] not in done]
    for p in blanks:
        out.write(json.dumps({"doc_id": p["doc_id"], "page_summary": "[blank page]",
                              "page_kind": "blank"}, ensure_ascii=False) + "\n")
    todo = [p for p in pages if p["doc_type"] != "blank" and p["doc_id"] not in done]
    print(f"[pagesum] {len(todo)} pages ({len(blanks)} blanks marked, {len(done)} done), "
          f"{a.workers} workers", flush=True)

    def work(p):
        text = "\n".join(b["text"] for b in p["blocks"])
        res = call(a.api, a.model, text)
        return {"doc_id": p["doc_id"], "page_summary": res.get("page_summary"),
                "page_kind": res.get("page_kind"), "error": res.get("_error")}

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for fut in as_completed([ex.submit(work, p) for p in todo]):
            rec = fut.result()
            with lock:
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
                if n % 250 == 0:
                    out.flush(); print(f"  ...{n}/{len(todo)}", flush=True)
    out.close()
    print(f"[pagesum] done {n}/{len(todo)}", flush=True)


if __name__ == "__main__":
    main()
