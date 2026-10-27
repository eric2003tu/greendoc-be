"""Read-only reference data that mirrors constants defined in the frontend:
templates (src/lib/templates.ts), styles (engine.ts), themes (design-tab.tsx),
page sizes (store.ts), margin presets (pagination.ts), fonts and shortcuts
(dialogs.tsx). Keep in sync when the frontend changes them."""

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

TEMPLATE_CATEGORIES = ["General", "Personal", "Business", "Academic", "Creative"]

PAGE_SIZES = {
    "A4": {"width": 794, "height": 1123, "label": "A4"},
    "Letter": {"width": 816, "height": 1056, "label": "Letter"},
    "Legal": {"width": 816, "height": 1344, "label": "Legal"},
    "A5": {"width": 559, "height": 794, "label": "A5"},
    "Executive": {"width": 864, "height": 1080, "label": "Executive"},
    "Tabloid": {"width": 1056, "height": 1632, "label": "Tabloid"},
}


def _m(top, right, bottom, left):
    return {"top": top, "right": right, "bottom": bottom, "left": left}


MARGIN_PRESETS = [
    {"id": "normal", "label": "Normal", "margins": _m(96, 96, 96, 96)},
    {"id": "narrow", "label": "Narrow", "margins": _m(72, 72, 72, 72)},
    {"id": "moderate", "label": "Moderate", "margins": _m(96, 144, 96, 144)},
    {"id": "wide", "label": "Wide", "margins": _m(96, 192, 96, 192)},
    {"id": "mirrored", "label": "Mirrored", "margins": _m(96, 96, 96, 192)},
]
CUSTOM_MARGINS = [
    {"id": "top", "label": "Top", "margins": _m(72, 96, 96, 96)},
    {"id": "bottom", "label": "Bottom", "margins": _m(96, 96, 72, 96)},
    {"id": "left", "label": "Left", "margins": _m(96, 96, 96, 144)},
    {"id": "right", "label": "Right", "margins": _m(96, 144, 96, 96)},
]
DEFAULT_MARGINS = _m(96, 96, 96, 96)

FONTS = [
    "Calibri", "Aptos", "Arial", "Segoe UI", "Times New Roman", "Courier New", "Georgia",
    "Verdana", "Tahoma", "Trebuchet MS", "Cambria", "Candara", "Consolas", "Constantia",
    "Corbel", "Franklin Gothic Book", "Garamond", "Impact", "Lucida Sans",
    "Palatino Linotype", "Rockwell", "Sitka Text",
]
FONT_SIZES = [8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 24, 28, 36, 48, 72]

SHORTCUTS = [
    ["Ctrl+N", "New document"],
    ["Ctrl+O", "Open a document"],
    ["Ctrl+S", "Save"],
    ["Ctrl+P", "Print"],
    ["Ctrl+F", "Find"],
    ["Ctrl+H", "Replace"],
    ["Ctrl+Z", "Undo"],
    ["Ctrl+Y", "Redo"],
    ["Ctrl+B / I / U", "Bold / Italic / Underline"],
    ["Ctrl+Shift+E", "Track changes"],
    ["Ctrl+Alt+M", "New comment"],
    ["Ctrl+Shift+G", "Word count"],
    ["F7", "Spelling and grammar"],
    ["Ctrl+Shift+8", "Show paragraph marks"],
]

DEFAULT_SOURCES = [
    {"tag": "Doe24", "type": "Journal Article", "author": "Doe, J.", "title": "On document automation", "year": "2024", "publisher": "Journal of Productivity"},
    {"tag": "Smith19", "type": "Book", "author": "Smith, A.", "title": "Modern office suites", "year": "2019", "publisher": "Greenleaf Press"},
    {"tag": "Lee22", "type": "Report", "author": "Lee, K.", "title": "Usability of ribbon interfaces", "year": "2022", "publisher": "Greenleaf Labs"},
]


@lru_cache(maxsize=1)
def templates():
    return json.loads((DATA / "templates.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def design():
    return json.loads((DATA / "design.json").read_text(encoding="utf-8"))


def template_by_id(template_id):
    return next((t for t in templates() if t["id"] == template_id), None)


def theme_ids():
    return {t["id"] for t in design()["themes"]}
