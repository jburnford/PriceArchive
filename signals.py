#!/usr/bin/env python3
"""Deterministic letter-boundary signal detectors (English + French).

Detects, on a page's reading-order blocks:
  - place + dateline ("Washington 4 Aug 1822", "NEW YORK May 6, 1926",
    "13 juin 1809")  -> primary letter START
  - salutation ("Dear Sir", "My Dear Price", "Messrs ...", "Monsieur")
  - (No. N) marker  -> forces a NEW letter boundary mid-page
  - closing ("Yours very truly", "Signed H. Shearer") -> letter END
Dates are parsed to ISO with precision (day|month|year) where possible; archaic
abbreviations (Octr, Decr) and French (accented or OCR-stripped) are handled.
"""
from __future__ import annotations
import re
import unicodedata

# ---------------------------------------------------------------- months -----
# token -> month number. Includes EN abbreviations/archaic and FR (accented +
# accent-stripped, since OCR often drops diacritics).
_MONTHS = {
    # English
    "january": 1, "jan": 1, "february": 2, "feb": 2, "febr": 2, "feb[y]": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9,
    "sep": 9, "sepr": 9, "october": 10, "oct": 10, "octr": 10, "november": 11,
    "nov": 11, "novr": 11, "december": 12, "dec": 12, "decr": 12, "decemr": 12,
    # French (accent-stripped keys; matching is done on stripped text)
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}
# month numbers for ISO; abbrev '.' is stripped before lookup
_MONTH_ALT = "|".join(sorted((re.escape(k.replace("[y]", "y")) for k in _MONTHS),
                             key=len, reverse=True)) + "|feby"
_YEAR = r"(?:18\d{2}|19[0-6]\d)"    # 1800-1969 (fonds spans ~1809-1950s)
_DAY = r"\d{1,2}"
_ORD = r"(?:st|nd|rd|th|e|er)?"

# DAY MONTH YEAR  (EN "4 Aug 1822", FR "13 juin 1809")
_RE_DMY = re.compile(
    rf"\b(?P<day>{_DAY}){_ORD}\.?\s+(?P<mon>{_MONTH_ALT})\.?\s*,?\s*(?P<year>{_YEAR})\b",
    re.I)
# MONTH DAY, YEAR  (EN "May 6, 1926")
_RE_MDY = re.compile(
    rf"\b(?P<mon>{_MONTH_ALT})\.?\s+(?P<day>{_DAY}){_ORD}\.?\s*,?\s*(?P<year>{_YEAR})\b",
    re.I)
# bare MONTH YEAR / just YEAR fallbacks
_RE_MY = re.compile(rf"\b(?P<mon>{_MONTH_ALT})\.?\s*,?\s*(?P<year>{_YEAR})\b", re.I)

_SALUTATION = re.compile(
    r"^\s*("
    r"my\s+dear\s+\w+|dear\s+sirs?|dear\s+madam|dear\s+messrs|"
    r"dear\s+(?:mr|mrs|miss|dr|messrs|sir)\.?\s*[\w.&'-]*|"
    r"gentlemen|sir[:,]|sirs[:,]|messrs\.?\s+[\w.&'-]|"
    r"monsieur|messieurs|cher\s+monsieur|chers?\s+messieurs|mon\s+cher\s+\w+"
    r")\b", re.I)

_NO_MARKER = re.compile(r"^\s*\(?\s*no\.?\s*(\d{1,3})\s*\)?", re.I)

_CLOSING = re.compile(
    r"\b("
    r"yours\s+(?:very\s+)?(?:truly|faithfully|sincerely|respectfully|obediently)|"
    r"your\s+obedient\s+servant|i\s+(?:am|remain)[, ].{0,40}?your|"
    r"signed[:,]?\s+[A-Z]|"
    r"votre\s+(?:tout\s+)?d[eé]vou[eé]|agr[eé]ez|je\s+demeure"
    r")", re.I)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _month_num(tok: str) -> int | None:
    t = strip_accents(tok).lower().strip(". ")
    if t == "feby":
        return 2
    return _MONTHS.get(t)


def parse_date(text: str) -> dict | None:
    """First parseable date in `text` -> {date_raw, date_iso, precision, span}.
    Matching is accent-insensitive (we search the accent-stripped string but map
    spans back to the original)."""
    flat = strip_accents(text)
    for rx, has_day in ((_RE_DMY, True), (_RE_MDY, True), (_RE_MY, False)):
        m = rx.search(flat)
        if not m:
            continue
        mon = _month_num(m.group("mon"))
        if not mon:
            continue
        year = int(m.group("year"))
        if has_day:
            day = int(m.group("day"))
            if 1 <= day <= 31:
                return {"date_raw": text[m.start():m.end()],
                        "date_iso": f"{year:04d}-{mon:02d}-{day:02d}",
                        "precision": "day", "span": (m.start(), m.end())}
        return {"date_raw": text[m.start():m.end()],
                "date_iso": f"{year:04d}-{mon:02d}", "precision": "month",
                "span": (m.start(), m.end())}
    # bare year (last resort)
    m = re.search(_YEAR, flat)
    if m:
        return {"date_raw": text[m.start():m.end()], "date_iso": text[m.start():m.end()],
                "precision": "year", "span": (m.start(), m.end())}
    return None


# function words that betray a prose date ("Before the First of June 1915")
# rather than a real dateline place ("Gaspé", "New York")
_PLACE_STOP = {"the", "of", "before", "after", "and", "in", "to", "a", "an",
               "for", "on", "at", "by", "with", "from", "received", "your", "my",
               "this", "that", "first", "day", "each", "or", "be", "is", "was",
               "shall", "will", "dated", "about", "until", "between", "during"}


def _plausible_place(place: str) -> bool:
    """A dateline place is a short proper noun ('Gaspé', 'NEW YORK', 'St. John').
    Reject leading prose ('Before the First of') so mid-sentence dates don't open
    spurious letters."""
    if not place:
        return True                      # bare date ('4 Aug 1822') is a fine dateline
    toks = place.split()
    if len(toks) > 4 or len(place) > 40:
        return False
    for t in toks:
        tl = t.strip(".,'&").lower()
        if tl and tl in _PLACE_STOP:
            return False
        if t[:1].isalpha() and not t[:1].isupper():
            return False                 # every place token starts uppercase
    return True


def find_dateline(text: str) -> dict | None:
    """A dateline: a DAY-precision date near the start of the block, preceded only
    by a plausible proper-noun place. Day precision + proper place together reject
    prose dates ('your letter of 18 June', 'Before the First of June 1915')."""
    d = parse_date(text)
    if not d or d["precision"] != "day":   # real datelines carry a day
        return None
    start = d["span"][0]
    if start > 60:                         # date too deep -> mid-body reference
        return None
    place = text[:start].strip(" ,.;:\n\t-")
    if not _plausible_place(place):
        return None
    return {"type": "place_date", "value": d["date_raw"], "place": place or None,
            "date_raw": d["date_raw"], "date_iso": d["date_iso"],
            "date_precision": d["precision"]}


def salutation(text: str) -> str | None:
    m = _SALUTATION.match(text)
    return m.group(0).strip() if m else None


def no_marker(text: str) -> int | None:
    m = _NO_MARKER.match(text)
    return int(m.group(1)) if m else None


def closing(text: str) -> str | None:
    m = _CLOSING.search(text)
    return m.group(0).strip() if m else None


def page_start_signals(blocks: list[dict], head_blocks: int = 3) -> list[dict]:
    """Start signals on a page. Datelines/salutations are position-gated to the
    first `head_blocks` of a run UNLESS preceded by a (No.N) marker (which can
    start a fresh letter mid-page). Returns list sorted by block order, each:
    {block_order, type, value, place?, date_raw?, date_iso?, date_precision?}."""
    sigs = []
    blocks = sorted(blocks, key=lambda b: b.get("order", 0))
    for idx, b in enumerate(blocks):
        order = b.get("order", idx)
        t = (b.get("text") or "").strip()
        if not t:
            continue
        n = no_marker(t)
        if n is not None:
            sigs.append({"block_order": order, "type": "no_marker", "value": f"(No.{n})",
                         "no_marker": n})
        # gate datelines/salutations to head of page OR right after a (No.N)
        near_head = idx < head_blocks or (sigs and sigs[-1]["type"] == "no_marker"
                                          and sigs[-1]["block_order"] == order - 1
                                          or any(s["type"] == "no_marker" for s in sigs))
        dl = find_dateline(t)
        if dl and near_head:
            sigs.append({"block_order": order, **dl})
        sal = salutation(t)
        if sal and (near_head or dl):
            sigs.append({"block_order": order, "type": "salutation", "value": sal})
    return sigs
