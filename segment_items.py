#!/usr/bin/env python3
"""Phase B: segment indexed pages into logical ITEMS.

An item is a logical archival unit spanning >=1 page:
  letter      - opened by a dateline/salutation/(No.N); absorbs continuation
                pages (no new start) until the next start.
  document    - a run of consecutive non-letter, non-blank pages (legal/corporate
                docs, reports, ledgers, genealogies) split on a title at page top.
  table       - a document item whose pages are all tables.
  finding_aid - the printed finding aid (M188-1 pp.1-19), grouped as one item.
  docket      - a short endorsement page acting as a separator.
Blank pages break runs and are indexed at page level only (not items).

Reads build/pages.jsonl, writes build/items.jsonl + build/letters/<id>/{json,md}.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

import signals as S

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"


def letter_open_positions(start_signals: list[dict]) -> list[dict]:
    """Collapse per-block signals into distinct letter-open positions. A dateline
    or salutation within a couple blocks of a just-opened letter belongs to it,
    not a new one. (No.N) always opens. Returns the opening signals in order."""
    opens, last = [], -99
    for s in sorted(start_signals, key=lambda x: x["block_order"]):
        o = s["block_order"]
        if s["type"] == "no_marker":
            opens.append(s); last = o
        elif s["type"] == "place_date":
            if o - last > 2:
                opens.append(s); last = o
            else:
                last = o  # dateline of the just-opened letter
        elif s["type"] == "salutation":
            if o - last > 3:
                opens.append(s); last = o
    return opens


class Item:
    def __init__(self, kind, page):
        self.kind = kind
        self.reel = page["reel"]
        self.page_start = page["page_num"]
        self.page_end = page["page_num"]
        self.pages = []                      # list of (doc_id, [block dicts])
        self.page_types = []                 # doc_type of each constituent page
        self.langs = []
        self.open_signal = None              # the start signal that opened a letter
        self.seq = ""                        # a/b/c for multiple letters per page
        self.has_closing = False             # seen a closing/signature yet?

    def add(self, page, lo=0, hi=None):
        blocks = [b for b in page["blocks"] if (hi is None or lo <= b["order"] < hi)
                  and b["order"] >= lo]
        if not blocks:
            return
        self.pages.append((page["doc_id"], blocks))
        self.page_end = page["page_num"]
        self.page_types.append(page["doc_type"])
        self.langs.append(page["language"])
        if not self.has_closing:
            self.has_closing = any(S.closing(b.get("text", "")) for b in blocks)

    # ---- finalize ----------------------------------------------------------
    def text(self) -> str:
        out = []
        for _doc, blocks in self.pages:
            for b in sorted(blocks, key=lambda x: x["order"]):
                if b.get("text"):
                    out.append(b["text"])
        return "\n".join(out)

    def all_blocks(self):
        for _doc, blocks in self.pages:
            for b in sorted(blocks, key=lambda x: x["order"]):
                yield b

    def block_list(self) -> list[dict]:
        """Flat per-item block list in reading order (index = position)."""
        out = []
        for doc, blocks in self.pages:
            for b in sorted(blocks, key=lambda x: x["order"]):
                out.append({"doc_id": doc, "order": b["order"],
                            "category": b["category"], "text": b.get("text", "")})
        return out

    def finalize(self) -> dict:
        txt = self.text()
        kind = self.kind
        # promote a document item whose pages are all tables
        if kind == "document" and self.page_types and \
                all(t == "table" for t in self.page_types):
            kind = "table"
        lang = max(set(self.langs), key=self.langs.count) if self.langs else "en"
        doc_ids = [d for d, _ in self.pages]
        rec = {"kind": kind, "reel": self.reel, "page_start": self.page_start,
               "page_end": self.page_end, "n_pages": len(self.pages),
               "doc_ids": doc_ids, "n_chars": len(txt), "language": lang,
               "text": txt}

        if kind == "letter":
            os_ = self.open_signal or {}
            rec["date_raw"] = os_.get("date_raw")
            rec["date_iso"] = os_.get("date_iso")
            rec["date_precision"] = os_.get("date_precision")
            rec["place"] = os_.get("place")
            rec["no_marker"] = os_.get("no_marker")
            rec["addressee"] = self._addressee()
            rec["signatory"], rec["closing"] = self._signoff()
            rec["confidence"], rec["review_flag"], rec["review_reasons"] = self._score(rec)
        else:
            rec["title"] = self._title()
            rec["confidence"] = 0.6
            rec["review_flag"] = (kind == "document" and len(self.pages) > 6)
            rec["review_reasons"] = ["long_document_run"] if rec["review_flag"] else []
        rec["_blocks"] = self.block_list()
        return rec

    def _addressee(self):
        # salutation text, else the short capitalized block right after the dateline
        for b in self.all_blocks():
            sal = S.salutation(b.get("text", ""))
            if sal:
                return sal
        return None

    def _signoff(self):
        blocks = list(self.all_blocks())
        for i, b in enumerate(blocks):
            c = S.closing(b.get("text", ""))
            if c:
                m = re.search(r"signed[:,]?\s+(.+)$", b["text"], re.I)
                sig = m.group(1).strip() if m else None
                if not sig and i + 1 < len(blocks):
                    nxt = blocks[i + 1]["text"].strip()
                    if 0 < len(nxt) <= 60:
                        sig = nxt
                return sig, c
        return None, None

    def _title(self):
        for b in self.all_blocks():
            if b.get("category") == "heading" and b.get("text"):
                return b["text"][:120]
        for b in self.all_blocks():
            if b.get("text"):
                return b["text"][:80]
        return None

    def _score(self, rec):
        s, reasons = 0.3, []
        if rec.get("date_iso"):
            s += 0.25
        else:
            reasons.append("no_date")
        if rec.get("addressee"):
            s += 0.15
        if rec.get("signatory") or rec.get("closing"):
            s += 0.2
        if len(self.pages) == 1:
            s += 0.1
        if len(set(self.langs)) > 1:
            s -= 0.05
        if len(self.pages) > 4:
            s -= 0.1; reasons.append("many_pages")
        if self.open_signal and self.open_signal["type"] == "salutation" \
                and not rec.get("date_iso"):
            s -= 0.2; reasons.append("salutation_only")
        s = max(0.0, min(1.0, s))
        flag = s < 0.5 or not rec.get("date_iso")
        if flag and "no_date" not in reasons and not rec.get("date_iso"):
            reasons.append("no_date")
        return round(s, 2), flag, reasons


def page_is_new_document(page) -> bool:
    """A non-letter page that starts a fresh document: first block is a heading."""
    blocks = sorted(page["blocks"], key=lambda b: b["order"])
    return bool(blocks and blocks[0].get("category") == "heading")


def segment(pages: list[dict]) -> list[dict]:
    pages.sort(key=lambda p: (p["reel"], p["page_num"]))
    items, cur = [], None

    def close():
        nonlocal cur
        if cur is not None:
            items.append(cur.finalize())
            cur = None

    for page in pages:
        dt = page["doc_type"]
        if dt == "blank":
            close(); continue
        if dt == "finding_aid":
            if cur and cur.kind == "finding_aid":
                cur.add(page)
            else:
                close(); cur = Item("finding_aid", page); cur.add(page)
            continue
        if dt == "docket":
            close(); cur = Item("docket", page); cur.add(page); close()
            continue

        opens = letter_open_positions(page.get("start_signals", []))
        if not opens:
            # no new letter on this page
            if cur and cur.kind == "letter":
                # a letter is "done" once its signoff appeared, or the next page is
                # a table / starts a new document -> don't keep absorbing.
                if cur.has_closing or dt == "table" or page_is_new_document(page):
                    close(); cur = Item("document", page); cur.add(page)
                else:
                    cur.add(page)                   # genuine letter continuation
            elif cur and cur.kind == "document" and not page_is_new_document(page):
                cur.add(page)                       # extend document run
            else:
                close(); cur = Item("document", page); cur.add(page)
            continue

        # >=1 letter starts on this page -> split
        first = opens[0]["block_order"]
        if cur is not None and first > 0:
            cur.add(page, lo=0, hi=first)           # tail of prior item
        close()
        for i, s in enumerate(opens):
            lo = s["block_order"]
            hi = opens[i + 1]["block_order"] if i + 1 < len(opens) else None
            cur = Item("letter", page)
            cur.open_signal = s
            cur.add(page, lo=lo, hi=hi)
            if i < len(opens) - 1:
                close()                             # all but last close on-page
    close()

    # assign ids (seq per page_start for multiple letters)
    seqcount = {}
    for it in items:
        ps = f"{it['reel']}_{it['page_start']:04d}"
        if it["kind"] == "letter":
            k = seqcount.get(ps, 0); seqcount[ps] = k + 1
            it["item_id"] = f"{ps}_L{chr(97 + k)}"
        elif it["kind"] == "finding_aid":
            it["item_id"] = f"FA_{ps}"
        elif it["kind"] == "docket":
            it["item_id"] = f"{ps}_K"
        else:
            it["item_id"] = f"{ps}_D"
    return items


def write_artifacts(items):
    ldir = BUILD / "letters"
    ldir.mkdir(parents=True, exist_ok=True)
    for it in items:
        if it["kind"] != "letter":
            continue
        d = ldir / it["item_id"]
        d.mkdir(exist_ok=True)
        (d / "letter.json").write_text(json.dumps(it, ensure_ascii=False, indent=1))
        hdr = [f"# Letter {it['item_id']}", "",
               f"- Date: {it.get('date_iso') or it.get('date_raw') or '?'}",
               f"- Place: {it.get('place') or '?'}",
               f"- To: {it.get('addressee') or '?'}",
               f"- From/Signed: {it.get('signatory') or '?'}",
               f"- Reel/pages: {it['reel']} {it['page_start']}-{it['page_end']}",
               f"- Language: {it['language']}  Confidence: {it['confidence']}", "", "---", ""]
        (d / "letter.md").write_text("\n".join(hdr) + it["text"], encoding="utf-8")


def main():
    pages = [json.loads(l) for l in (BUILD / "pages.jsonl").open()]
    items = segment(pages)
    with (BUILD / "items.jsonl").open("w", encoding="utf-8") as fi, \
            (BUILD / "item_blocks.jsonl").open("w", encoding="utf-8") as fb:
        for it in items:
            blocks = it.pop("_blocks", [])
            fb.write(json.dumps({"item_id": it["item_id"], "blocks": blocks},
                                ensure_ascii=False) + "\n")
            fi.write(json.dumps(it, ensure_ascii=False) + "\n")
    write_artifacts(items)
    from collections import Counter
    kinds = Counter(it["kind"] for it in items)
    letters = [it for it in items if it["kind"] == "letter"]
    dated = sum(1 for it in letters if it.get("date_iso"))
    flagged = sum(1 for it in letters if it.get("review_flag"))
    print(f"[segment] {len(items)} items -> build/items.jsonl")
    print("[segment] kinds:", dict(kinds))
    if letters:
        print(f"[segment] letters: {len(letters)} | dated: {dated} "
              f"({100*dated//len(letters)}%) | review_flag: {flagged}")
        import statistics as st
        print(f"[segment] letter pages: med={int(st.median([it['n_pages'] for it in letters]))} "
              f"max={max(it['n_pages'] for it in letters)}; "
              f"letter words med={int(st.median([len(it['text'].split()) for it in letters]))}")


if __name__ == "__main__":
    main()
