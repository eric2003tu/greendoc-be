"""Server-side HTML sanitiser, mirroring the frontend's sanitizeHtmlFragment.

Documents are shared publicly via links, so stored HTML must never carry
scripts or event handlers to the people who open it."""

import re
from html import escape
from html.parser import HTMLParser

DROP_WITH_CONTENT = {"script", "iframe", "object", "embed", "form"}
DROP_TAGS = {"base", "meta", "link"}
VOID = {"br", "hr", "img", "input", "col", "wbr", "source", "area"}
BAD_URL = re.compile(r"^\s*(javascript|vbscript):", re.I)
BAD_DATA = re.compile(r"^\s*data:(?!image/(png|jpe?g|gif|webp|bmp))", re.I)
BAD_STYLE = re.compile(r"expression\s*\(|javascript\s*:", re.I)
URL_ATTRS = {"href", "src", "xlink:href", "action", "formaction", "poster"}


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out = []
        self.skip_depth = 0
        self.skip_tag = None

    def _attrs(self, attrs):
        parts = []
        for name, value in attrs:
            lname = name.lower()
            value = value or ""
            if lname.startswith("on"):
                continue
            if lname in URL_ATTRS and (BAD_URL.match(value) or BAD_DATA.match(value)):
                continue
            if lname == "style" and BAD_STYLE.search(value):
                continue
            parts.append(f' {name}="{escape(value, quote=True)}"')
        return "".join(parts)

    def handle_starttag(self, tag, attrs):
        if self.skip_depth:
            if tag == self.skip_tag:
                self.skip_depth += 1
            return
        if tag in DROP_WITH_CONTENT:
            self.skip_tag, self.skip_depth = tag, 1
            return
        if tag in DROP_TAGS:
            return
        self.out.append(f"<{tag}{self._attrs(attrs)}>")

    def handle_startendtag(self, tag, attrs):
        if self.skip_depth or tag in DROP_WITH_CONTENT or tag in DROP_TAGS:
            return
        self.out.append(f"<{tag}{self._attrs(attrs)}>")

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag == self.skip_tag:
                self.skip_depth -= 1
            return
        if tag in DROP_TAGS or tag in VOID:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip_depth:
            self.out.append(data)

    def handle_entityref(self, name):
        if not self.skip_depth:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip_depth:
            self.out.append(f"&#{name};")


def sanitize_html(html: str) -> str:
    parser = _Sanitizer()
    parser.feed(html)
    parser.close()
    return "".join(parser.out)
