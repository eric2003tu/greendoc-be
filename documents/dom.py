"""Minimal HTML tree (parse, query, edit, serialise) built on the stdlib parser.

The editor stores documents as HTML, so every server-side feature (stats,
table of contents, find and replace, track changes, mail merge, export) works
on this tree rather than on regular expressions."""

from html import escape
from html.parser import HTMLParser

VOID = {"br", "hr", "img", "input", "col", "wbr", "source", "area", "meta", "link", "base"}
BLOCK = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "table", "tr",
    "blockquote", "pre", "section", "header", "footer", "figure", "figcaption",
}


class Node:
    __slots__ = ("tag", "attrs", "children", "text", "parent")

    def __init__(self, tag=None, attrs=None, text=None):
        self.tag = tag  # None => text node (or the root when text is None)
        self.attrs = dict(attrs or {})
        self.children = []
        self.text = text
        self.parent = None

    @property
    def is_text(self):
        return self.tag is None and self.text is not None

    def append(self, child):
        child.parent = self
        self.children.append(child)

    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def has_class(self, name):
        return name in self.classes()

    def walk(self):
        yield self
        for child in list(self.children):
            yield from child.walk()

    def find_all(self, predicate):
        return [n for n in self.walk() if n is not self and predicate(n)]

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def inside_class(self, *names):
        return any(a.classes() & set(names) for a in self.ancestors())

    def text_content(self):
        if self.is_text:
            return self.text
        return "".join(c.text_content() for c in self.children)

    def remove(self):
        if self.parent:
            self.parent.children.remove(self)
            self.parent = None

    def unwrap(self):
        """Replace this element by its children."""
        parent = self.parent
        if parent is None:
            return
        index = parent.children.index(self)
        for child in self.children:
            child.parent = parent
        parent.children[index : index + 1] = self.children
        self.parent = None

    def replace_children(self, children):
        for child in children:
            child.parent = self
        self.children = list(children)

    def to_html(self):
        if self.is_text:
            return escape(self.text, quote=False)
        inner = "".join(c.to_html() for c in self.children)
        if self.tag is None:
            return inner
        attrs = "".join(
            f' {k}="{escape(v, quote=True)}"' if v is not None else f" {k}"
            for k, v in self.attrs.items()
        )
        if self.tag in VOID:
            return f"<{self.tag}{attrs}>"
        return f"<{self.tag}{attrs}>{inner}</{self.tag}>"


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].append(Node(tag, attrs))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].append(Node(text=data))


def parse(html):
    builder = _Builder()
    builder.feed(html or "")
    builder.close()
    return builder.root


def text_node(text):
    return Node(text=text)


def element(tag, text=None, **attrs):
    node = Node(tag, {k.replace("_", "-"): v for k, v in attrs.items()})
    if text is not None:
        node.append(text_node(text))
    return node


def html_to_text(html):
    """Plain text with one line per block, tabs between table cells."""
    root = parse(html)
    out = []

    def emit(node):
        if node.is_text:
            out.append(node.text.replace("\xa0", " "))
            return
        if node.tag == "br":
            out.append("\n")
            return
        if node.tag in ("script", "style"):
            return
        if node.tag in ("td", "th") and out and not out[-1].endswith(("\n", "\t")):
            out.append("\t")
        for child in node.children:
            emit(child)
        if node.tag in BLOCK and out and not out[-1].endswith("\n"):
            out.append("\n")

    emit(root)
    return "".join(out).strip("\n")
