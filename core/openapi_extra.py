"""OpenAPI descriptions for the editor-feature endpoints (review, mailings,
references, versions, export, catalog, preferences). Merged into the base spec
by core.openapi.build_spec."""

from .openapi import COMMON_ERRORS, SECURED, UUID_PARAM_DOC, _error, _json

ERR = {
    "400": "Missing or invalid `X-Client-Id` header, or invalid request.",
    "403": "The document is protected (Review > Restrict Editing).",
    "404": "Not found, or it belongs to a different client.",
    "409": "Conflict: duplicate id or a limit was reached.",
    "413": "Input too large.",
    "422": "The content could not be processed.",
}


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def path_param(name, description, fmt=None):
    schema = {"type": "string"}
    if fmt:
        schema["format"] = fmt
    return {"name": name, "in": "path", "required": True, "description": description, "schema": schema}


CID = path_param("cid", "Comment id (the `data-cid` value in the document HTML).")
RID = path_param("rid", "Reply or revision id.")
VERSION = path_param("version_id", "Version id.", "uuid")


def query(name, description, schema=None, required=False, example=None):
    p = {"name": name, "in": "query", "required": required, "description": description, "schema": schema or {"type": "string"}}
    if example is not None:
        p["example"] = example
    return p


def op(tag, summary, operation_id, description="", ok=None, errors=(), body=None, params=None,
       secured=True, body_example=None, ok_code="200", ok_description="Success.", multipart=None):
    operation = {"tags": [tag], "summary": summary, "operationId": operation_id}
    if description:
        operation["description"] = description
    if secured:
        operation["security"] = SECURED
    if params:
        operation["parameters"] = params
    if body:
        operation["requestBody"] = {"required": True, "content": _json(body, body_example)}
    if multipart:
        operation["requestBody"] = {"required": True, "content": multipart}
    responses = {}
    if ok:
        responses[ok_code] = {"description": ok_description, "content": _json(ok)}
    elif isinstance(ok_code, str):
        responses[ok_code] = {"description": ok_description}
    for code in errors:
        responses[code] = _error(ERR[code])
    responses.update(COMMON_ERRORS)
    operation["responses"] = responses
    return operation


def build_paths():
    d = [UUID_PARAM_DOC]
    owned = ("400", "404")
    return {
        "/api/documents/import/": {
            "post": op(
                "Documents", "Open a file from disk", "importDocument",
                "Upload a `.docx`, `.html`/`.htm` or `.txt` file (10 MB max). Files exported by GreenDoc "
                "reopen exactly; other `.docx` files are converted from their paragraphs, headings, "
                "formatting and tables. Creates a new document.",
                ok="Document", ok_code="201", ok_description="Created from the file.",
                errors=("400", "409", "422"),
                multipart={"multipart/form-data": {"schema": {"type": "object", "required": ["file"],
                           "properties": {"file": {"type": "string", "format": "binary"}}}}},
            )
        },
        "/api/documents/{doc_id}/duplicate/": {
            "parameters": d,
            "post": op("Documents", "Duplicate a document", "duplicateDocument",
                       "Copies content, layout settings and properties (not comments or the share link).",
                       ok="Document", ok_code="201", ok_description="The copy.", errors=("400", "404", "409")),
        },
        "/api/recent/": {
            "get": op("Documents", "Recent documents", "listRecent",
                      "File > Open > Recent: the last 8 documents saved or opened, newest first.",
                      ok="RecentList", errors=("400",)),
            "delete": op("Documents", "Clear recent documents", "clearRecent",
                         "Empties the Recent list. The documents themselves are kept and return to the list when saved again.",
                         ok=None, ok_code="204", ok_description="Cleared.", errors=("400",)),
        },
        "/api/documents/{doc_id}/stats/": {
            "parameters": d,
            "get": op("Document tools", "Word count statistics", "documentStats",
                      "Words, characters, paragraphs, lines and pages. Lines and pages are estimates "
                      "(layout only exists in the browser) unless you pass the real `pages` count.",
                      ok="Stats", errors=owned,
                      params=[query("pages", "Actual page count from the editor.", {"type": "integer", "minimum": 1})]),
        },
        "/api/documents/{doc_id}/toc/": {
            "parameters": d,
            "get": op("Document tools", "Build a table of contents", "documentToc",
                      "Generated from the document's headings; `html` is ready to insert.",
                      ok="Toc", errors=owned,
                      params=[query("levels", "Heading levels to include.", example="1,2,3")]),
        },
        "/api/documents/{doc_id}/find/": {
            "parameters": d,
            "post": op("Document tools", "Find and replace", "findInDocument",
                       "Searches the document text (table of contents, bibliography and notes are skipped). "
                       "When `replacement` is present, every match is replaced and the document saved.",
                       body="FindInput", ok="FindResult", errors=("400", "403", "404"),
                       body_example={"query": "colour", "matchCase": False, "wholeWord": True, "replacement": "color"}),
        },
        "/api/documents/{doc_id}/spellcheck/": {
            "parameters": d,
            "get": op("Document tools", "Spellcheck a saved document", "documentSpellcheck",
                      ok="SpellcheckResult", errors=owned),
        },
        "/api/documents/{doc_id}/readability/": {
            "parameters": d,
            "get": op("Document tools", "Readability of a saved document", "documentReadability",
                      ok="Readability", errors=owned),
        },
        "/api/documents/{doc_id}/export/": {
            "parameters": d,
            "get": {
                **op("Document tools", "Export / download", "exportDocument",
                     "File > Export / Save As. `docx` embeds a GreenDoc snapshot so it reopens losslessly here. "
                     "PDF is produced in the browser (Print dialog), not by this API.",
                     errors=owned,
                     params=[query("format", "File format.", {"type": "string", "enum": ["docx", "html", "txt", "rtf", "xml"], "default": "docx"})]),
                "responses": {
                    "200": {
                        "description": "The file as an attachment.",
                        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
                    },
                    "400": _error(ERR["400"]),
                    "404": _error(ERR["404"]),
                    **COMMON_ERRORS,
                },
            },
        },
        "/api/documents/{doc_id}/versions/": {
            "parameters": d,
            "get": op("Version history", "List versions", "listVersions",
                      "Automatic snapshots are taken on save (at most one per 10 minutes, 30 kept) plus manual ones (50 kept).",
                      ok="VersionList", errors=owned),
            "post": op("Version history", "Save a named version", "createVersion",
                       body="VersionInput", ok="Version", ok_code="201", ok_description="Snapshot created.",
                       errors=owned, body_example={"label": "Before sending to client"}),
        },
        "/api/documents/{doc_id}/versions/{version_id}/": {
            "parameters": d + [VERSION],
            "get": op("Version history", "Get a version", "getVersion", ok="VersionDetail", errors=owned),
            "delete": op("Version history", "Delete a version", "deleteVersion",
                         ok=None, ok_code="204", ok_description="Deleted.", errors=owned),
        },
        "/api/documents/{doc_id}/versions/{version_id}/restore/": {
            "parameters": d + [VERSION],
            "post": op("Version history", "Restore a version", "restoreVersion",
                       "Replaces the current content with the version. The current state is saved first as 'Before restore', so this can be undone.",
                       ok="Document", errors=("400", "403", "404")),
        },
        "/api/documents/{doc_id}/mailmerge/": {
            "parameters": d,
            "get": op("Mailings", "Get recipients", "getMergeRecipients",
                      "Mailings > Select Recipients data, plus the merge fields found in the document.",
                      ok="MergeDataset", errors=owned),
            "put": op("Mailings", "Set recipients", "setMergeRecipients",
                      "Send either `csv` (first row = field names) or `fields` + `records`. Up to 5,000 records and 100 fields.",
                      body="MergeInput", ok="MergeDataset", errors=("400", "404"),
                      body_example={"csv": "FirstName,LastName,Email\nAvery,Jordan,avery@example.com"}),
            "delete": op("Mailings", "Remove recipients", "clearMergeRecipients",
                         ok=None, ok_code="204", ok_description="Removed.", errors=owned),
        },
        "/api/documents/{doc_id}/mailmerge/preview/": {
            "parameters": d,
            "get": op("Mailings", "Preview one recipient", "previewMerge",
                      "Fills the merge fields with one recipient. The index wraps around (Next/Previous Record).",
                      ok="MergePreview", errors=("400", "404", "409"),
                      params=[query("index", "Recipient index (0-based).", {"type": "integer", "default": 0})]),
        },
        "/api/documents/{doc_id}/mailmerge/finish/": {
            "parameters": d,
            "get": {
                **op("Mailings", "Finish & merge", "finishMerge",
                     "One merged copy per recipient, each page-broken. Downloads an HTML file, or JSON with `format=json`.",
                     errors=("400", "404", "409"),
                     params=[query("format", "`html` (download) or `json`.", {"type": "string", "enum": ["html", "json"], "default": "html"})]),
                "responses": {
                    "200": {
                        "description": "The merged documents.",
                        "content": {
                            "text/html": {"schema": {"type": "string"}},
                            "application/json": {"schema": {"type": "object", "properties": {"count": {"type": "integer"}, "html": {"type": "string"}}}},
                        },
                    },
                    "400": _error(ERR["400"]),
                    "404": _error(ERR["404"]),
                    "409": _error("No recipients selected."),
                    **COMMON_ERRORS,
                },
            },
        },
        "/api/documents/{doc_id}/comments/": {
            "parameters": d,
            "get": op("Comments", "List comments", "listComments", ok="CommentList", errors=owned,
                      params=[query("resolved", "Filter by resolved state.", {"type": "string", "enum": ["true", "false"]})]),
            "post": op("Comments", "Add a comment", "addComment",
                       "Pass the same `id` you embed in the HTML (`data-cid`) so the two stay linked; one is generated otherwise.",
                       body="CommentInput", ok="Comment", ok_code="201", ok_description="Created.",
                       errors=("400", "404", "409"),
                       body_example={"id": "cmt-lk3j2-1", "text": "Please double-check this figure.", "quote": "42%"}),
        },
        "/api/documents/{doc_id}/comments/{cid}/": {
            "parameters": d + [CID],
            "get": op("Comments", "Get a comment", "getComment", ok="Comment", errors=owned),
            "put": op("Comments", "Edit or resolve a comment", "updateComment",
                      body="CommentUpdate", ok="Comment", errors=("400", "404"), body_example={"resolved": True}),
            "delete": op("Comments", "Delete a comment", "deleteComment",
                         "Also removes the comment's highlight marker from the document text.",
                         ok=None, ok_code="204", ok_description="Deleted.", errors=owned),
        },
        "/api/documents/{doc_id}/comments/{cid}/replies/": {
            "parameters": d + [CID],
            "post": op("Comments", "Reply to a comment", "replyToComment",
                       body="ReplyInput", ok="Comment", ok_code="201", ok_description="The comment with the new reply.",
                       errors=("400", "404", "409"), body_example={"text": "Fixed, thanks."}),
        },
        "/api/documents/{doc_id}/comments/{cid}/replies/{rid}/": {
            "parameters": d + [CID, RID],
            "delete": op("Comments", "Delete a reply", "deleteReply",
                         ok=None, ok_code="204", ok_description="Deleted.", errors=owned),
        },
        "/api/documents/{doc_id}/revisions/": {
            "parameters": d,
            "get": op("Track changes", "List tracked changes", "listRevisions", ok="RevisionList", errors=owned),
            "post": op("Track changes", "Record a tracked change", "addRevision",
                       "Use the same `id` as the `data-rev` attribute in the HTML.",
                       body="RevisionInput", ok="Revision", ok_code="201", ok_description="Recorded.",
                       errors=("400", "404", "409"), body_example={"kind": "insert", "text": "new wording"}),
        },
        "/api/documents/{doc_id}/revisions/accept-all/": {
            "parameters": d,
            "post": op("Track changes", "Accept all changes", "acceptAllRevisions",
                       "Keeps insertions, removes deletions, and clears the change list.",
                       ok="RevisionResolution", errors=("400", "403", "404")),
        },
        "/api/documents/{doc_id}/revisions/reject-all/": {
            "parameters": d,
            "post": op("Track changes", "Reject all changes", "rejectAllRevisions",
                       "Removes insertions, restores deletions, and clears the change list.",
                       ok="RevisionResolution", errors=("400", "403", "404")),
        },
        "/api/documents/{doc_id}/revisions/{rid}/": {
            "parameters": d + [RID],
            "delete": op("Track changes", "Forget a tracked change", "deleteRevision",
                         "Removes the list entry only; use accept-all / reject-all to change the text.",
                         ok=None, ok_code="204", ok_description="Removed.", errors=owned),
        },
        "/api/proofing/stats/": {
            "post": op("Proofing", "Word count for text or HTML", "textStats",
                       "Review > Word Count for content that is not saved yet. Send `html` or `text`.",
                       secured=False, body="StatsInput", ok="Stats", errors=("413",),
                       body_example={"text": "One two three."}),
        },
        "/api/catalog/templates/": {
            "get": op("Catalog", "List templates", "listTemplates",
                      "The 17 start-screen templates (without bodies).", secured=False, ok="TemplateList",
                      params=[query("category", "General, Personal, Business, Academic, Creative or All."),
                              query("q", "Search name and description.")]),
        },
        "/api/catalog/templates/{template_id}/": {
            "get": op("Catalog", "Get a template", "getTemplate",
                      "Includes the HTML body. To start a document from it, `POST /api/documents/` with `templateId`.",
                      secured=False, ok="Template", errors=("404",),
                      params=[path_param("template_id", "Template id.")]),
        },
        "/api/catalog/design/": {
            "get": op("Catalog", "Document themes and paragraph styles", "getDesign",
                      "Design tab themes and the Styles gallery.", secured=False, ok="Design"),
        },
        "/api/catalog/page-setup/": {
            "get": op("Catalog", "Page setup options", "getPageSetup",
                      "Paper sizes (pixels at 96 dpi), margin presets and orientations.", secured=False, ok="PageSetup"),
        },
        "/api/catalog/reference/": {
            "get": op("Catalog", "Fonts, sizes and keyboard shortcuts", "getReference", secured=False, ok="Reference"),
        },
        "/api/preferences/": {
            "get": op("Preferences", "Get my preferences", "getPreferences",
                      "Defaults are filled in for anything not set.", ok="Preferences", errors=("400",)),
            "put": op("Preferences", "Update my preferences", "updatePreferences",
                      "Partial update; unknown keys are rejected.", body="Preferences", ok="Preferences",
                      errors=("400",), body_example={"dark": True, "authorName": "Merci Ruyanga"}),
            "delete": op("Preferences", "Reset preferences", "resetPreferences",
                         ok=None, ok_code="204", ok_description="Reset to defaults.", errors=("400",)),
        },
        "/api/sources/": {
            "get": op("References", "List sources", "listSources",
                      "Your bibliography library. Three sample sources are added the first time it is read.",
                      ok="SourceList", errors=("400",)),
            "post": op("References", "Add a source", "addSource", body="Source", ok="Source",
                       ok_code="201", ok_description="Created.", errors=("400", "409"),
                       body_example={"tag": "Khan21", "type": "Book", "author": "Khan, R.", "title": "Writing well", "year": "2021", "publisher": "Green Press"}),
        },
        "/api/sources/{tag}/": {
            "parameters": [path_param("tag", "Source tag.")],
            "get": op("References", "Get a source", "getSource", ok="Source", errors=owned),
            "put": op("References", "Edit a source", "updateSource", body="Source", ok="Source", errors=("400", "404", "409")),
            "delete": op("References", "Delete a source", "deleteSource", ok=None, ok_code="204", ok_description="Deleted.", errors=owned),
        },
        "/api/citations/format/": {
            "post": op("References", "Format a citation", "formatCitation",
                       "Returns the in-text citation and the HTML the editor inserts.", body="CitationInput",
                       ok="Citation", errors=("400", "404"), body_example={"tag": "Doe24", "style": "APA"}),
        },
        "/api/bibliography/": {
            "get": op("References", "Build the bibliography", "getBibliography",
                      "HTML for all sources, ready to insert.", ok="Bibliography", errors=("400",)),
        },
    }


def s_obj(props, required=None, description=None):
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    if description:
        schema["description"] = description
    return schema


STR, BOOL, INT = {"type": "string"}, {"type": "boolean"}, {"type": "integer"}
DATE = {"type": "string", "format": "date-time"}
STRS = {"type": "array", "items": STR}
MARGINS = s_obj({k: {"type": "integer", "minimum": 0, "maximum": 400} for k in ("top", "right", "bottom", "left")},
                description="Pixels at 96 dpi.")


def build_schemas():
    settings = s_obj(
        {
            "pageSize": {"type": "string", "enum": ["A4", "Letter", "Legal", "A5", "Executive", "Tabloid"]},
            "orientation": {"type": "string", "enum": ["portrait", "landscape"]},
            "margins": MARGINS,
            "gutter": {"type": "integer", "minimum": 0, "maximum": 400},
            "columns": {"type": "integer", "minimum": 1, "maximum": 6},
            "lineBetween": BOOL, "showLineNumbers": BOOL,
            "verticalAlign": {"type": "string", "enum": ["top", "center", "justify"]},
            "pageNumbers": BOOL, "pageBorders": BOOL,
            "pageColor": {"type": "string", "example": "#ffffff", "description": "#rrggbb"},
            "watermarkText": {"type": "string", "maxLength": 100},
            "watermarkSemitransparent": BOOL, "watermarkWashout": BOOL,
            "headerText": {"type": "string", "maxLength": 500}, "footerText": {"type": "string", "maxLength": 500},
            "trackChanges": BOOL,
            "theme": {"type": "string", "description": "A design id from /api/catalog/design/, or empty."},
            "defaultFont": STR, "defaultFontSize": {"type": "number"},
            "language": {"type": "string", "example": "en-US"},
        },
        description="Page layout and editor state saved with the document. All keys optional; unknown keys are rejected. Updates merge into the stored settings.",
    )
    properties = s_obj({k: {"type": "string", "maxLength": 200} for k in ("subject", "author", "keywords", "company", "category")},
                       description="File > Info > Properties.")
    comment_fields = {"author": STR, "initials": STR, "date": DATE, "text": STR}
    return {
        "Settings": settings,
        "Properties": properties,
        "Stats": s_obj({"words": INT, "characters": INT, "charactersWithSpaces": INT, "paragraphs": INT,
                        "lines": INT, "pages": INT, "estimated": {**BOOL, "description": "True when lines/pages are server estimates."}}),
        "StatsInput": s_obj({"text": STR, "html": STR}, description="Provide `html` or `text`."),
        "Toc": s_obj({"entries": {"type": "array", "items": s_obj({"level": INT, "text": STR, "anchor": STR})}, "html": STR}),
        "FindInput": s_obj({"query": {"type": "string", "maxLength": 500}, "matchCase": BOOL, "wholeWord": BOOL,
                            "replacement": {"type": "string", "description": "If present, replaces all matches and saves."}},
                           required=["query"]),
        "FindResult": s_obj({"count": INT, "replaced": INT,
                             "matches": {"type": "array", "items": s_obj({"text": STR, "context": STR})},
                             "html": {**STR, "description": "Updated document (only when replaced)."}}),
        "RecentList": s_obj({"recent": {"type": "array", "items": s_obj(
            {"id": {**STR, "format": "uuid"}, "title": STR, "template": STR, "modified": DATE,
             "path": {**STR, "description": "File name it was opened from, if any."}})}}),
        "Version": s_obj({"id": {**STR, "format": "uuid"}, "title": STR, "label": STR, "automatic": BOOL, "createdAt": DATE}),
        "VersionDetail": {"allOf": [ref("Version"), s_obj({"html": STR, "settings": ref("Settings")})]},
        "VersionList": s_obj({"versions": {"type": "array", "items": ref("Version")}}),
        "VersionInput": s_obj({"label": {"type": "string", "maxLength": 100}}),
        "MergeInput": s_obj({"csv": {**STR, "description": "First row = field names."},
                             "fields": STRS, "records": {"type": "array", "items": {"type": "object", "additionalProperties": STR}}}),
        "MergeDataset": s_obj({"fields": STRS, "records": {"type": "array", "items": {"type": "object", "additionalProperties": STR}},
                               "count": INT, "fieldsInDocument": {**STRS, "description": "Merge fields placed in the document."}}),
        "MergePreview": s_obj({"index": INT, "count": INT, "html": STR}),
        "CommentReply": s_obj({"id": STR, **comment_fields}),
        "Comment": s_obj({"id": STR, **comment_fields, "replies": {"type": "array", "items": ref("CommentReply")},
                          "resolved": BOOL, "quote": {**STR, "description": "The text the comment is attached to."}}),
        "CommentList": s_obj({"comments": {"type": "array", "items": ref("Comment")}}),
        "CommentInput": s_obj({"id": {**STR, "maxLength": 64}, "text": {"type": "string", "maxLength": 5000},
                               "quote": {"type": "string", "maxLength": 2000},
                               "author": {"type": "string", "maxLength": 60, "description": "Defaults to your preferences author name."}},
                              required=["text"]),
        "CommentUpdate": s_obj({"text": {"type": "string", "maxLength": 5000}, "resolved": BOOL}),
        "ReplyInput": s_obj({"id": {**STR, "maxLength": 64}, "text": {"type": "string", "maxLength": 5000}, "author": STR},
                            required=["text"]),
        "Revision": s_obj({"id": STR, "kind": {"type": "string", "enum": ["insert", "delete", "format"]},
                           "author": STR, "initials": STR, "date": DATE, "text": STR}),
        "RevisionList": s_obj({"trackChanges": BOOL, "revisions": {"type": "array", "items": ref("Revision")}}),
        "RevisionInput": s_obj({"id": STR, "kind": {"type": "string", "enum": ["insert", "delete", "format"]},
                                "text": {"type": "string", "maxLength": 5000}, "author": STR}, required=["kind"]),
        "RevisionResolution": s_obj({"resolved": INT, "action": {"type": "string", "enum": ["accepted", "rejected"]}, "html": STR}),
        "TemplateSummary": s_obj({"id": STR, "name": STR, "desc": STR, "category": STR}),
        "Template": {"allOf": [ref("TemplateSummary"), s_obj({"html": STR})]},
        "TemplateList": s_obj({"categories": STRS, "templates": {"type": "array", "items": ref("TemplateSummary")}}),
        "Design": s_obj({
            "themes": {"type": "array", "items": s_obj({"id": STR, "name": STR, "bodyFont": STR, "headingFont": STR, "accent": STR, "soft": STR})},
            "styles": {"type": "array", "items": s_obj({"name": STR, "tag": STR, "className": STR, "size": {"type": "number"},
                                                         "bold": BOOL, "italic": BOOL, "color": STR, "before": {"type": "number"}, "after": {"type": "number"}})},
        }),
        "PageSetup": s_obj({
            "pageSizes": {"type": "array", "items": s_obj({"id": STR, "label": STR, "width": INT, "height": INT})},
            "orientations": STRS,
            "marginPresets": {"type": "array", "items": s_obj({"id": STR, "label": STR, "margins": MARGINS})},
            "customMargins": {"type": "array", "items": s_obj({"id": STR, "label": STR, "margins": MARGINS})},
            "defaultMargins": MARGINS, "citationStyles": STRS,
        }),
        "Reference": s_obj({"fonts": STRS, "fontSizes": {"type": "array", "items": {"type": "number"}},
                            "shortcuts": {"type": "array", "items": {"type": "array", "items": STR, "minItems": 2, "maxItems": 2}}}),
        "Preferences": s_obj({
            "dark": BOOL, "autoSave": BOOL, "showRuler": BOOL, "showStatusBar": BOOL,
            "authorName": {"type": "string", "maxLength": 60}, "defaultDesign": STR,
            "defaultFont": STR, "defaultFontSize": {"type": "number"}, "language": STR,
            "ribbonDisplay": {"type": "string", "enum": ["autoHide", "tabs", "commands"]},
            "view": {"type": "string", "enum": ["print", "web", "outline", "draft", "reading"]},
            "zoom": {"type": "integer", "minimum": 10, "maximum": 500},
            "showGrid": BOOL, "showParagraphMarks": BOOL, "showWhitespace": BOOL,
        }),
        "Source": s_obj({"tag": {"type": "string", "maxLength": 40}, "type": {"type": "string", "example": "Book"},
                         "author": {"type": "string", "example": "Doe, J."}, "title": STR, "year": STR, "publisher": STR},
                        required=["tag", "author", "title"]),
        "SourceList": s_obj({"sources": {"type": "array", "items": ref("Source")}}),
        "CitationInput": s_obj({"tag": STR, "style": {"type": "string", "enum": ["APA", "MLA", "Chicago"], "default": "APA"}}, required=["tag"]),
        "Citation": s_obj({"tag": STR, "style": STR, "text": {**STR, "example": "(Doe, 2024)"}, "html": STR}),
        "Bibliography": s_obj({"count": INT, "html": STR}),
    }


def extend(spec):
    spec["paths"].update(build_paths())
    schemas = spec["components"]["schemas"]
    schemas.update(build_schemas())

    # Documents now carry layout settings, properties and flags.
    extra_input = {
        "templateId": {"type": "string", "description": "Create only: start from a catalogue template (see /api/catalog/templates/)."},
        "settings": ref("Settings"),
        "properties": ref("Properties"),
        "protected": {"type": "boolean", "description": "Review > Restrict Editing. While true, html changes are rejected (403)."},
        "markedFinal": {"type": "boolean", "description": "File > Info > Mark as Final."},
    }
    schemas["DocumentInput"]["properties"].update(extra_input)
    schemas["DocumentSummary"]["properties"].update(
        protected=BOOL, markedFinal=BOOL, sourceName={**STR, "description": "File name the document was opened from."}
    )
    schemas["Document"]["allOf"][1]["properties"].update(settings=ref("Settings"), properties=ref("Properties"))
    schemas["SharedDocument"]["properties"]["settings"] = ref("Settings")

    spec["tags"].extend(
        [
            {"name": "Document tools", "description": "Stats, table of contents, find & replace, proofing and export for a saved document."},
            {"name": "Comments", "description": "Review pane comments and replies."},
            {"name": "Track changes", "description": "Tracked insertions, deletions and formatting."},
            {"name": "Mailings", "description": "Mail merge recipients, preview and finish."},
            {"name": "References", "description": "Bibliography sources and citations."},
            {"name": "Version history", "description": "Automatic and named snapshots with restore."},
            {"name": "Catalog", "description": "Read-only reference data: templates, themes, styles, page sizes, fonts and shortcuts."},
            {"name": "Preferences", "description": "Per-browser settings such as dark mode and author name."},
        ]
    )
    return spec
