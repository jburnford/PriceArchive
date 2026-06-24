#!/usr/bin/env python3
"""Canonical block schema for OCR output, and markup-quarantine logic.

VENDORED verbatim from wpcs-ocr/benchmark/canonical.py so this repo is
self-contained. Tool outputs normalize to one schema:

  doc = {"doc_id": str, "corpus": str, "meta": {...},
         "pages": [ {"page": int, "blocks": [ block, ... ]} ]}
  block = {"order": int, "category": str, "text": str (plain, no markup),
           "score": bool, "bbox": [x0,y0,x1,y1]?, "cells": [[str,...],...]?}
"""
from __future__ import annotations
import re
from html.parser import HTMLParser

CATEGORIES = {
    "body", "heading", "page_number", "header", "footer", "footnote",
    "caption", "table", "figure", "marginalia", "list",
}
DEFAULT_UNSCORED = {"page_number", "header", "footer", "figure"}

_BLOCK_HTML = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5",
               "h6", "table", "thead", "tbody", "ul", "ol", "blockquote",
               "section", "article", "hr"}
_DROP_HTML = {"script", "style", "img"}


class _HTMLStripper(HTMLParser):
    """Drop tags, decode entities, insert newlines at block boundaries."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_HTML:
            self._skip += 1
        elif tag in ("td", "th"):
            self.out.append(" ")
        elif tag in _BLOCK_HTML:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in _DROP_HTML and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_HTML:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)

    def text(self) -> str:
        return "".join(self.out)


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_HEADER = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.M)
_MD_BLOCKQUOTE = re.compile(r"^[ \t]*>[ \t]?", re.M)
_MD_LIST = re.compile(r"^[ \t]*([*+-]|\d+\.)[ \t]+", re.M)
_MD_RULE = re.compile(r"^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$", re.M)
_MD_FENCE = re.compile(r"^```.*$", re.M)
_MD_EMPH = re.compile(r"(\*\*|__|\*|_|~~|`)")
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+.!$~>|-])")
_TABLE_SEP_ROW = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def clean_markup(text: str | None) -> str:
    """HTML + Markdown -> plain content."""
    if not text:
        return ""
    if "<" in text and ">" in text:
        s = _HTMLStripper()
        s.feed(text)
        text = s.text()
    text = _MD_FENCE.sub("", text)
    text = _MD_IMAGE.sub(" ", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEADER.sub("", text)
    text = _MD_BLOCKQUOTE.sub("", text)
    text = _MD_LIST.sub("", text)
    text = _MD_RULE.sub("", text)
    lines = []
    for ln in text.splitlines():
        if _TABLE_SEP_ROW.match(ln):
            continue
        if ln.strip().startswith("|") or ("|" in ln and ln.count("|") >= 2):
            ln = " ".join(c.strip() for c in ln.split("|") if c.strip())
        lines.append(ln)
    text = "\n".join(lines)
    text = _MD_ESCAPE.sub(r"\1", text)
    text = _MD_EMPH.sub("", text)
    return text.strip()


class _TableCells(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def cells_from_html(html: str) -> list[list[str]]:
    p = _TableCells()
    p.feed(html)
    return [r for r in p.rows if any(c for c in r)]


def flatten(doc: dict, categories: set[str] | None = None, sep: str = "\n") -> str:
    """Reading-order text of scoreable blocks."""
    parts = []
    for page in doc.get("pages", []):
        for b in sorted(page.get("blocks", []), key=lambda x: x.get("order", 0)):
            if b.get("score", True) is False:
                continue
            if categories is not None and b.get("category") not in categories:
                continue
            t = b.get("text", "")
            if t:
                parts.append(t)
    return sep.join(parts)


def all_cells(doc: dict) -> list[list[str]]:
    out = []
    for page in doc.get("pages", []):
        for b in page.get("blocks", []):
            if b.get("category") == "table" and b.get("cells"):
                out.extend(b["cells"])
    return out
