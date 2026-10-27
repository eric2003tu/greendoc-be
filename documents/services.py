"""Pure document operations. No Django imports; each function takes and returns
HTML strings or plain data, so they are easy to test and to extend with ML."""

import csv
import io
import math
import re

from . import dom

WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)
NON_SEARCHABLE = ("w-toc", "w-bibliography", "w-footnotes", "w-endnotes")

LINES_PER_PAGE = 46
CHARS_PER_LINE = 90


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------


def document_stats(html, pages=None):
    """Mirrors the frontend's computeStats. `lines` and `pages` are estimates
    because real layout only exists in the browser."""
    root = dom.parse(html)
    text = dom.html_to_text(html)
    words = len(WORD_RE.findall(text))
    paragraphs = [
        c for c in root.children if not c.is_text and c.text_content().strip()
    ]
    lines = sum(max(1, math.ceil(len(p.text_content().strip()) / CHARS_PER_LINE)) for p in paragraphs)
    return {
        "words": words,
        "characters": len(re.sub(r"\s", "", text)),
        "charactersWithSpaces": len(text),
        "paragraphs": len(paragraphs),
        "lines": lines,
        "pages": pages or max(1, math.ceil(lines / LINES_PER_PAGE)),
        "estimated": pages is None,
    }


# --------------------------------------------------------------------------
# Table of contents
# --------------------------------------------------------------------------


def build_toc(html, levels=(1, 2, 3)):
    root = dom.parse(html)
    headings = [
        h
        for h in root.find_all(lambda n: n.tag in ("h1", "h2", "h3", "h4", "h5", "h6"))
        if int(h.tag[1]) in levels and not h.inside_class("w-toc")
    ]
    entries = []
    for i, h in enumerate(headings):
        entries.append(
            {
                "level": int(h.tag[1]),
                "text": h.text_content().strip(),
                "anchor": h.attrs.get("id") or f"toc-{i}",
            }
        )
    if not entries:
        return {"entries": [], "html": "<p>No table of contents entries found.</p>"}
    parts = ['<div class="w-toc"><p class="w-toc-title">Table of Contents</p>']
    for e in entries:
        parts.append(
            f'<p class="w-toc-entry w-toc-l{e["level"]}">'
            f'<a href="#{dom.escape(e["anchor"])}">{dom.escape(e["text"], quote=False)}</a></p>'
        )
    parts.append("</div>")
    return {"entries": entries, "html": "".join(parts)}


# --------------------------------------------------------------------------
# Find and replace
# --------------------------------------------------------------------------


def _pattern(query, match_case, whole_word):
    body = re.escape(query)
    if whole_word:
        body = rf"(?<!\w){body}(?!\w)"
    return re.compile(body, 0 if match_case else re.IGNORECASE)


def find_replace(html, query, match_case=False, whole_word=False, replacement=None):
    """Returns (count, matches, new_html). Replaces only when `replacement` is a string."""
    pattern = _pattern(query, match_case, whole_word)
    root = dom.parse(html)
    matches = []
    count = 0
    for node in root.find_all(lambda n: n.is_text):
        if node.inside_class(*NON_SEARCHABLE) or not node.text.strip():
            continue
        found = list(pattern.finditer(node.text))
        if not found:
            continue
        count += len(found)
        if len(matches) < 500:
            for m in found:
                start = max(0, m.start() - 20)
                matches.append(
                    {
                        "text": m.group(0),
                        "context": node.text[start : m.end() + 20],
                    }
                )
        if replacement is not None:
            node.text = pattern.sub(lambda _m: replacement, node.text)
    return count, matches[:500], root.to_html() if replacement is not None else html


# --------------------------------------------------------------------------
# Track changes
# --------------------------------------------------------------------------


def resolve_revisions(html, accept):
    """Accept or reject every tracked change, as the Review tab's buttons do."""
    root = dom.parse(html)
    keep_class, drop_class = ("w-rev-ins", "w-rev-del") if accept else ("w-rev-del", "w-rev-ins")
    for node in root.find_all(lambda n: n.has_class(drop_class)):
        node.remove()
    for node in root.find_all(lambda n: n.has_class(keep_class)):
        node.unwrap()
    return root.to_html()


def strip_comment_refs(html, comment_id):
    """Remove a deleted comment's highlight marker but keep the text it wrapped."""
    root = dom.parse(html)
    for node in root.find_all(lambda n: n.has_class("w-comment-ref") and n.attrs.get("data-cid") == comment_id):
        node.unwrap()
    return root.to_html()


# --------------------------------------------------------------------------
# Mail merge
# --------------------------------------------------------------------------

MAX_RECORDS = 5000
MAX_FIELDS = 100
MAX_CELL = 1000


class MergeError(ValueError):
    pass


def parse_csv(text):
    """Header row names the fields; mirrors the frontend's parseCsv."""
    rows = [r for r in csv.reader(io.StringIO(text.strip())) if any(c.strip() for c in r)]
    if len(rows) < 2:
        return [], []
    fields = [f.strip() for f in rows[0] if f.strip()]
    records = [
        {f: (row[i].strip() if i < len(row) else "") for i, f in enumerate(fields)}
        for row in rows[1:]
    ]
    return fields, records


def validate_dataset(fields, records):
    if not isinstance(fields, list) or not all(isinstance(f, str) and f.strip() for f in fields):
        raise MergeError("fields must be a list of non-empty strings.")
    if len(fields) > MAX_FIELDS or len(set(fields)) != len(fields):
        raise MergeError(f"fields must be unique and at most {MAX_FIELDS}.")
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise MergeError(f"records must be a list of at most {MAX_RECORDS} rows.")
    clean = []
    for record in records:
        if not isinstance(record, dict):
            raise MergeError("every record must be an object.")
        clean.append(
            {f: str(record.get(f, ""))[:MAX_CELL] for f in fields}
        )
    return [f.strip() for f in fields], clean


def merge_fields_in(html):
    root = dom.parse(html)
    seen = []
    for n in root.find_all(lambda n: n.has_class("w-merge-field")):
        name = n.attrs.get("data-merge-field", "")
        if name and name not in seen:
            seen.append(name)
    return seen


def merge_record(html, record):
    """One merged copy: fills «fields» and evaluates If-Then-Else rules."""
    root = dom.parse(html)
    for n in root.find_all(lambda n: n.has_class("w-merge-field")):
        n.replace_children([dom.text_node(record.get(n.attrs.get("data-merge-field", ""), ""))])
    for n in root.find_all(lambda n: n.has_class("w-merge-rule")):
        field = n.attrs.get("data-field", "")
        expected = n.attrs.get("data-equals", "")
        chosen = n.attrs.get("data-if-true", "") if record.get(field, "") == expected else n.attrs.get("data-if-false", "")
        n.replace_children([dom.text_node(chosen)])
    return root.to_html()


def preview_record(html, record):
    """Preview keeps unknown fields visible as <<name>> like the Mailings tab."""
    root = dom.parse(html)
    for n in root.find_all(lambda n: n.has_class("w-merge-field")):
        name = n.attrs.get("data-merge-field", "")
        n.replace_children([dom.text_node(record[name] if name in record else f"<<{name}>>")])
    return root.to_html()


MERGED_CSS = (
    "body{font:11pt Calibri,Arial,sans-serif;color:#222}"
    ".merged-copy{max-width:7in;margin:0 auto 1in;page-break-after:always}"
    "table{border-collapse:collapse;width:100%}td,th{border:1px solid #aaa;padding:6px}"
    "@media print{.merged-copy{margin:0 auto}}"
)


def merge_all(html, records):
    sections = "".join(f'<section class="merged-copy">{merge_record(html, r)}</section>' for r in records)
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Merged documents</title>'
        f"<style>{MERGED_CSS}</style></head><body>{sections}</body></html>"
    )


# --------------------------------------------------------------------------
# Citations and bibliography
# --------------------------------------------------------------------------

CITATION_STYLES = ("APA", "MLA", "Chicago")


def format_citation(source, style="APA"):
    last = source["author"].split(",")[0]
    if style == "APA":
        return f"({last}, {source['year']})"
    if style == "MLA":
        return f"({last})"
    return f"{source['author']} \"{source['title']}\" ({source['year']})"


def citation_html(source, style="APA"):
    tag = dom.escape(source["tag"])
    return (
        f'<span class="w-citation" data-tag="{tag}">'
        f"[{dom.escape(format_citation(source, style), quote=False)}]</span>&nbsp;"
    )


def bibliography_html(sources):
    items = "".join(
        f"<li>{dom.escape(s['author'], quote=False)} \"{dom.escape(s['title'], quote=False)}.\" "
        f"{dom.escape(s['publisher'], quote=False)}, {dom.escape(s['year'], quote=False)}.</li>"
        for s in sources
    )
    return f'<div class="w-bibliography"><p class="w-toc-title">Bibliography</p><ol>{items}</ol></div>'
