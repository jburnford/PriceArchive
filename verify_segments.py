#!/usr/bin/env python3
"""Phase D: verify the catalog and segmentation quality.

- date-parser unit asserts (EN/FR/archaic)
- distributions (items by kind, dated %, decade hist, pages/item, review reasons)
- coverage: every non-blank page belongs to an item
- round-trip char check (item chars vs page chars)
- writes build/verify_report.md and a build/review_sample.md of stratified letters
"""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

import duckdb
import signals as S

BUILD = Path(__file__).resolve().parent / "build"


def unit_dates():
    cases = [("22nd November 1816", "1816-11-22"), ("May 6, 1926", "1926-05-06"),
             ("13 juin 1809", "1809-06-13"), ("09 décembre 1922", "1922-12-09"),
             ("Montreal 11 Octr 1844", "1844-10-11")]
    out = []
    for t, e in cases:
        d = S.parse_date(t)
        out.append((t, e, d["date_iso"] if d else None, bool(d and d["date_iso"] == e)))
    return out


def main():
    con = duckdb.connect(str(BUILD / "catalog.duckdb"), read_only=True)
    L = []
    L.append("# Price Archive — segmentation verification\n")

    L.append("## Date parser unit checks")
    ok = True
    for t, e, got, passed in unit_dates():
        ok &= passed
        L.append(f"- {'PASS' if passed else 'FAIL'} `{t}` -> {got} (exp {e})")
    L.append(f"\n**date parser: {'ALL PASS' if ok else 'FAILURES'}**\n")

    kinds = con.execute("SELECT kind,count(*) FROM items GROUP BY kind ORDER BY 2 DESC").fetchall()
    L.append("## Items by kind\n" + "\n".join(f"- {k}: {n}" for k, n in kinds))

    nl = con.execute("SELECT count(*) FROM letters").fetchone()[0]
    nd = con.execute("SELECT count(*) FROM letters WHERE date_iso IS NOT NULL").fetchone()[0]
    rng = con.execute("SELECT min(date_iso),max(date_iso) FROM letters WHERE length(date_iso)>=7").fetchone()
    L.append(f"\n## Letters\n- total: {nl}\n- dated: {nd} ({100*nd//max(nl,1)}%)\n- date range: {rng[0]} .. {rng[1]}")

    dec = con.execute("""SELECT substr(date_iso,1,3)||'0s' AS decade, count(*) n
                         FROM letters WHERE length(date_iso)>=4 GROUP BY decade ORDER BY decade""").fetchall()
    L.append("\n### Dated letters by decade\n" + "\n".join(f"- {d}: {n}" for d, n in dec))

    pp = con.execute("SELECT min(n_pages),median(n_pages),max(n_pages) FROM letters").fetchone()
    L.append(f"\n- letter pages: min={pp[0]} median={pp[1]} max={pp[2]}")

    rf = con.execute("SELECT count(*) FROM items WHERE review_flag").fetchone()[0]
    L.append(f"\n## Review queue\n- items flagged: {rf}")
    reasons = Counter()
    for (r,) in con.execute("SELECT review_reasons FROM items WHERE review_flag AND review_reasons<>''").fetchall():
        for x in r.split(", "):
            reasons[x] += 1
    L.append("\n".join(f"- {k}: {n}" for k, n in reasons.most_common()))

    # coverage: every non-blank page assigned to an item
    miss = con.execute("""SELECT count(*) FROM pages p WHERE p.doc_type<>'blank'
        AND NOT EXISTS (SELECT 1 FROM item_pages ip WHERE ip.doc_id=p.doc_id)""").fetchone()[0]
    blanks = con.execute("SELECT count(*) FROM pages WHERE doc_type='blank'").fetchone()[0]
    L.append(f"\n## Coverage\n- non-blank pages not in any item: {miss} (should be 0)\n- blank pages (page-level only): {blanks}")

    # round-trip chars
    pc = con.execute("SELECT sum(n_chars) FROM pages").fetchone()[0]
    ic = con.execute("SELECT sum(n_chars) FROM items").fetchone()[0]
    L.append(f"- page chars: {pc:,} | item chars: {ic:,} | ratio: {ic/max(pc,1):.2f}")

    # (No.N) sequences
    seqs = con.execute("""SELECT reel,page_start,no_marker FROM items
        WHERE no_marker IS NOT NULL ORDER BY reel,page_start""").fetchall()
    L.append(f"\n## (No.N) markers: {len(seqs)} letters carry one")

    (BUILD / "verify_report.md").write_text("\n".join(L), encoding="utf-8")

    # stratified review sample (high + low confidence letters)
    samp = con.execute("""(SELECT * FROM letters WHERE review_flag ORDER BY random() LIMIT 15)
        UNION ALL (SELECT * FROM letters WHERE NOT review_flag AND date_iso IS NOT NULL
        ORDER BY random() LIMIT 15)""").fetchdf()
    rs = ["# Review sample (15 flagged + 15 clean letters)\n"]
    for _, r in samp.iterrows():
        rs.append(f"## {r['item_id']}  ({'FLAG' if r['review_flag'] else 'ok'} conf={r['confidence']})")
        rs.append(f"- date={r['date_iso']} place={r['place']} to={r['addressee']} "
                  f"sig={r['signatory']} pages={r['reel']} {r['page_start']}-{r['page_end']}")
        rs.append(f"- reasons: {r['review_reasons']}")
        rs.append("```\n" + (r['text_content'] or "")[:600] + "\n```\n")
    (BUILD / "review_sample.md").write_text("\n".join(rs), encoding="utf-8")

    print("\n".join(L))
    print(f"\n[verify] wrote build/verify_report.md and build/review_sample.md")


if __name__ == "__main__":
    main()
