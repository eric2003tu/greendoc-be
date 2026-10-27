"""Export and import of document files (docx, html, txt, rtf, xml).

DOCX files embed `word/greendoc.json` (same as the frontend's exporter), so a
file exported by either side reopens losslessly in the other. Files from other
editors are converted from their WordprocessingML paragraphs."""

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

from . import dom

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "rtf": "application/rtf",
    "xml": "application/xml; charset=utf-8",
}
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_UNZIPPED_BYTES = 50 * 1024 * 1024


def safe_filename(name):
    return re.sub(r'[\\/:*?"<>|]', "-", name).strip() or "Document"


def px_to_twips(px):
    return int(round(px * 15))


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def to_txt(html):
    return dom.html_to_text(html)


def to_html(html, title):
    # Same shell as the frontend's "Save as web page".
    return (
        '<!DOCTYPE html>\n<html xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">\n'
        f'<head><meta charset="utf-8"><title>{dom.escape(title, quote=False)}</title>\n'
        "<style>body{font-family:Calibri,sans-serif;font-size:11pt;line-height:1.08;margin:1in;} "
        "table{border-collapse:collapse;} td{border:1px solid #000;padding:4px;}</style>\n"
        f"</head><body>{html}</body></html>"
    )


def _lines(html):
    root = dom.parse(html)
    blocks = root.find_all(lambda n: n.tag in ("p", "h1", "h2", "h3", "li"))
    lines = [b.text_content() for b in blocks]
    return lines if lines else [dom.html_to_text(html)]


def _rtf_escape(value):
    out = []
    for ch in value:
        code = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\line ")
        elif ch == "\r":
            continue
        elif ch == "\t":
            out.append("\\tab ")
        elif code > 127:
            out.append(f"\\u{code - 65536 if code > 32767 else code}?")
        elif code >= 32:
            out.append(ch)
    return "".join(out)


def to_rtf(html):
    body = "\\par\n".join(_rtf_escape(line) for line in _lines(html))
    return f"{{\\rtf1\\ansi\\deff0{{\\fonttbl{{\\f0 Calibri;}}}}\\viewkind4\\uc1\\pard\\f0\\fs22 {body}\\par}}"


def to_xml(html):
    paras = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{xml_escape(line)}</w:t></w:r></w:p>' for line in _lines(html)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<?mso-application progid="Word.Document"?>\n'
        '<w:wordDocument xmlns:w="http://schemas.microsoft.com/office/word/2003/wordml">'
        f"<w:body>{paras}<w:sectPr/></w:body></w:wordDocument>"
    )


def _runs(node, bold=False, italic=False, underline=False, strike=False):
    """Inline content -> <w:r> elements, carrying bold/italic/underline down the tree."""
    out = []
    if node.is_text:
        text = node.text.replace("\xa0", " ")
        if text:
            props = "".join(
                [
                    "<w:b/>" if bold else "",
                    "<w:i/>" if italic else "",
                    '<w:u w:val="single"/>' if underline else "",
                    "<w:strike/>" if strike else "",
                ]
            )
            rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
            out.append(f'<w:r>{rpr}<w:t xml:space="preserve">{xml_escape(text)}</w:t></w:r>')
        return out
    if node.tag == "br":
        return ["<w:r><w:br/></w:r>"]
    if node.tag in ("script", "style", "img", "svg"):
        return out
    flags = dict(
        bold=bold or node.tag in ("b", "strong"),
        italic=italic or node.tag in ("i", "em"),
        underline=underline or node.tag == "u",
        strike=strike or node.tag in ("s", "strike", "del"),
    )
    for child in node.children:
        out.extend(_runs(child, **flags))
    return out


HEADING_STYLE = {"h1": "Heading1", "h2": "Heading2", "h3": "Heading3"}


def _paragraph(node, style=None, prefix=""):
    ppr = ""
    if style:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
    align = (node.attrs.get("style") or "")
    m = re.search(r"text-align:\s*(left|center|right|justify)", align)
    if m and not style:
        ppr = f'<w:pPr><w:jc w:val="{"both" if m.group(1) == "justify" else m.group(1)}"/></w:pPr>'
    runs = _runs(node)
    if prefix:
        runs.insert(0, f'<w:r><w:t xml:space="preserve">{xml_escape(prefix)}</w:t></w:r>')
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"


def _block(node):
    if node.is_text:
        return _paragraph(dom.element("p", node.text)) if node.text.strip() else ""
    tag = node.tag
    if tag in HEADING_STYLE:
        return _paragraph(node, HEADING_STYLE[tag])
    if tag == "blockquote":
        return _paragraph(node, "Quote")
    if tag in ("ul", "ol"):
        parts = []
        for i, li in enumerate([c for c in node.children if c.tag == "li"], 1):
            parts.append(_paragraph(li, "ListParagraph", prefix="• " if tag == "ul" else f"{i}. "))
        return "".join(parts)
    if tag == "table":
        rows = []
        for tr in node.find_all(lambda n: n.tag == "tr"):
            cells = "".join(
                f"<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/></w:tcPr>{_paragraph(td)}</w:tc>"
                for td in tr.children
                if td.tag in ("td", "th")
            )
            if cells:
                rows.append(f"<w:tr>{cells}</w:tr>")
        borders = "".join(
            f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
            for s in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        return f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{borders}</w:tblBorders></w:tblPr>{"".join(rows)}</w:tbl><w:p/>'
    if tag in ("div", "section") and any(not c.is_text and c.tag in ("p", "h1", "h2", "h3", "ul", "ol", "table") for c in node.children):
        return "".join(_block(c) for c in node.children)
    if tag in ("script", "style") or node.has_class("w-page-spacer"):
        return ""
    return _paragraph(node)


STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
    '<w:spacing w:after="160" w:line="259" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    + "".join(
        f'<w:style w:type="paragraph" w:styleId="Heading{i}"><w:name w:val="heading {i}"/><w:basedOn w:val="Normal"/>'
        f'<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:outlineLvl w:val="{i - 1}"/></w:pPr>'
        f'<w:rPr><w:b/><w:color w:val="{c}"/><w:sz w:val="{s}"/></w:rPr></w:style>'
        for i, c, s in ((1, "2F5496", 32), (2, "2F5496", 26), (3, "1F3763", 24))
    )
    + '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:ind w:left="720" w:right="720"/></w:pPr><w:rPr><w:i/><w:color w:val="404040"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>'
    '<w:qFormat/><w:pPr><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style></w:styles>'
)


def to_docx(html, title, author="GreenDoc", settings=None, page=None):
    """page = {"width": px, "height": px} resolved by the caller from settings."""
    settings = settings or {}
    page = page or {"width": 794, "height": 1123}
    margins = settings.get("margins") or {"top": 96, "right": 96, "bottom": 96, "left": 96}
    root = dom.parse(html)
    body = "".join(_block(c) for c in root.children) or "<w:p/>"
    landscape = ' w:orient="landscape"' if settings.get("orientation") == "landscape" else ""
    sect = (
        f'<w:sectPr><w:pgSz w:w="{px_to_twips(page["width"])}" w:h="{px_to_twips(page["height"])}"{landscape}/>'
        f'<w:pgMar w:top="{px_to_twips(margins["top"])}" w:right="{px_to_twips(margins["right"])}" '
        f'w:bottom="{px_to_twips(margins["bottom"])}" w:left="{px_to_twips(margins["left"])}" w:header="720" '
        f'w:footer="720" w:gutter="{px_to_twips(settings.get("gutter", 0))}"/></w:sectPr>'
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}{sect}</w:body></w:document>'
    )
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/><Default Extension="json" ContentType="application/json"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        "</Relationships>"
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rIdStyles" Type="{rel}/styles" Target="styles.xml"/></Relationships>'
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{xml_escape(title)}</dc:title><dc:creator>{xml_escape(author)}</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>'
    )
    snapshot = json.dumps({"title": title, "html": html, "version": 1})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("docProps/core.xml", core)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/styles.xml", STYLES_XML)
        z.writestr("word/greendoc.json", snapshot)
    return buf.getvalue()


def export(fmt, html, title, **kwargs):
    if fmt == "docx":
        return to_docx(html, title, **kwargs)
    if fmt == "html":
        return to_html(html, title).encode("utf-8")
    if fmt == "txt":
        return to_txt(html).encode("utf-8")
    if fmt == "rtf":
        return to_rtf(html).encode("ascii")
    if fmt == "xml":
        return to_xml(html).encode("utf-8")
    raise ValueError(f"Unsupported format: {fmt}")


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


class ImportError_(ValueError):
    pass


def _w(tag):
    return f"{{{W}}}{tag}"


def _docx_run_html(run):
    text = "".join(
        ("\t" if c.tag == _w("tab") else "<br>" if c.tag == _w("br") else dom.escape(c.text or "", quote=False))
        for c in run
        if c.tag in (_w("t"), _w("tab"), _w("br"))
    )
    if not text:
        return ""
    rpr = run.find(_w("rPr"))
    if rpr is not None:
        def on(name):
            el = rpr.find(_w(name))
            return el is not None and el.get(_w("val")) not in ("0", "false")

        if on("b"):
            text = f"<b>{text}</b>"
        if on("i"):
            text = f"<i>{text}</i>"
        if rpr.find(_w("u")) is not None and rpr.find(_w("u")).get(_w("val")) != "none":
            text = f"<u>{text}</u>"
        if on("strike"):
            text = f"<s>{text}</s>"
    return text


def _docx_paragraph_html(p):
    inner = "".join(_docx_run_html(r) for r in p.iter(_w("r")))
    style = ""
    ppr = p.find(_w("pPr"))
    if ppr is not None and ppr.find(_w("pStyle")) is not None:
        style = (ppr.find(_w("pStyle")).get(_w("val")) or "").lower()
    if style in ("heading1", "title"):
        return f"<h1>{inner}</h1>"
    if style == "heading2":
        return f"<h2>{inner}</h2>"
    if style == "heading3":
        return f"<h3>{inner}</h3>"
    if style == "quote":
        return f"<blockquote>{inner}</blockquote>"
    return f"<p>{inner or '<br>'}</p>"


def _docx_body_html(body):
    parts = []
    for child in body:
        if child.tag == _w("p"):
            parts.append(_docx_paragraph_html(child))
        elif child.tag == _w("tbl"):
            rows = "".join(
                "<tr>"
                + "".join(
                    "<td>" + "".join(_docx_paragraph_html(p) for p in tc.findall(_w("p"))) + "</td>"
                    for tc in tr.findall(_w("tc"))
                )
                + "</tr>"
                for tr in child.findall(_w("tr"))
            )
            parts.append(f'<table class="w-table"><tbody>{rows}</tbody></table>')
    return "".join(parts)


def docx_to_html(data):
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportError_("The file is not a valid .docx document.")
    with z:
        if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED_BYTES:
            raise ImportError_("The document expands to too large a size.")
        names = set(z.namelist())
        if "word/greendoc.json" in names:
            try:
                snap = json.loads(z.read("word/greendoc.json"))
                if isinstance(snap.get("html"), str):
                    return snap["html"], str(snap.get("title") or "")
            except ValueError:
                pass
        if "word/document.xml" not in names:
            raise ImportError_("The .docx file has no document body.")
        try:
            tree = ET.fromstring(z.read("word/document.xml"))  # stdlib parser: no external entities
        except ET.ParseError:
            raise ImportError_("The .docx document body could not be read.")
        title = ""
        if "docProps/core.xml" in names:
            try:
                core = ET.fromstring(z.read("docProps/core.xml"))
                el = core.find("{http://purl.org/dc/elements/1.1/}title")
                title = (el.text or "") if el is not None else ""
            except ET.ParseError:
                pass
    body = tree.find(_w("body"))
    return (_docx_body_html(body) if body is not None else ""), title


def import_file(filename, data):
    """Returns (html, title, kind). Raises ImportError_ for unsupported or bad files."""
    if len(data) > MAX_IMPORT_BYTES:
        raise ImportError_("File is too large (10 MB maximum).")
    name = filename or "Document"
    base = re.sub(r"\.[^.]+$", "", name) or "Document"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext == "docx":
        html, title = docx_to_html(data)
        return html, title or base, "docx"
    text = data.decode("utf-8", errors="replace")
    if ext in ("html", "htm"):
        body = re.search(r"<body[^>]*>(.*)</body>", text, re.I | re.S)
        t = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
        return (body.group(1) if body else text), (t.group(1).strip() if t else "") or base, "html"
    if ext in ("txt", "md", "csv", ""):
        paras = "".join(f"<p>{dom.escape(line, quote=False) or '<br>'}</p>" for line in text.splitlines())
        return paras or "<p><br></p>", base, "txt"
    raise ImportError_(f"Unsupported file type: .{ext}. Use .docx, .html, .htm or .txt.")
