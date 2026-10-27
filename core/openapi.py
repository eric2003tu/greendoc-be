"""OpenAPI 3.0 description of the GreenDoc API.

Hand-written because the views are plain Django (no DRF). core/tests/test_docs.py
fails if a route exists that is not described here, so the docs cannot drift."""

UUID_PARAM_DOC = {
    "name": "doc_id",
    "in": "path",
    "required": True,
    "description": "Document id (UUID). Use the same id as the frontend's local draft id.",
    "schema": {"type": "string", "format": "uuid"},
}


def _error(description):
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    }


def _json(schema_ref, example=None):
    content = {"schema": {"$ref": f"#/components/schemas/{schema_ref}"}}
    if example is not None:
        content["example"] = example
    return {"application/json": content}


COMMON_ERRORS = {
    "429": {
        "description": "Rate limit exceeded (per IP per minute). See the Retry-After header.",
        "headers": {
            "Retry-After": {
                "description": "Seconds until the limit window resets.",
                "schema": {"type": "integer"},
            }
        },
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    }
}

OWNER_ERRORS = {
    "400": _error("Missing or invalid `X-Client-Id` header, or invalid request body."),
    "404": _error("Document not found, or it belongs to a different client."),
    **COMMON_ERRORS,
}

# Either a guest id or a signed-in token identifies the caller.
SECURED = [{"ClientId": []}, {"BearerAuth": []}]


def _base_spec(server_url):
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "GreenDoc API",
            "version": "1.0.0",
            "description": (
                "Backend for the GreenDoc word processor.\n\n"
                "**Sign-in is optional, like Word.** Everything works as a guest: the browser makes a "
                "random UUID once and sends it in `X-Client-Id`. Creating an account or signing in "
                "(`/api/auth/*`) returns a token to send as `Authorization: Bearer <token>`; it keeps "
                "your documents on every device and brings your guest documents along. Either way you "
                "can only see and change your own documents. Click **Authorize** to try the protected "
                "endpoints.\n\n"
                "**Sharing:** a link can be `view` (read-only) or `edit` (anyone with the link can "
                "change the text). Links are unguessable tokens; signed-in users can also email invitations.\n\n"
                "**Errors** always have the shape `{\"error\": {\"code\", \"message\"}}`.\n\n"
                "**Limits:** documents up to 5 MB, 200 per client, proofing text up to "
                "200,000 characters, and a per-IP request rate limit.\n\n"
                "**Coverage:** documents with page layout and properties, comments, track changes, "
                "mail merge, bibliography, version history, recent files, import/export "
                "(docx, html, txt, rtf, xml), templates, themes, styles, preferences and proofing."
            ),
        },
        "servers": [{"url": server_url}],
        "tags": [
            {"name": "System", "description": "Service status."},
            {"name": "Documents", "description": "Create, read, save and delete your documents."},
            {"name": "Sharing", "description": "Public read-only share links."},
            {"name": "Proofing", "description": "Spelling, thesaurus and readability tools."},
        ],
        "paths": {
            "/api/health/": {
                "get": {
                    "tags": ["System"],
                    "summary": "Health check",
                    "description": "Verifies the API is up and can reach the database.",
                    "operationId": "health",
                    "responses": {
                        "200": {
                            "description": "Service is healthy.",
                            "content": _json(
                                "Health", {"status": "ok", "service": "greendoc-api"}
                            ),
                        },
                        **COMMON_ERRORS,
                    },
                }
            },
            "/api/documents/": {
                "get": {
                    "tags": ["Documents"],
                    "summary": "List my documents",
                    "description": "Newest first. The `html` body is omitted; fetch a document to get it.",
                    "operationId": "listDocuments",
                    "security": SECURED,
                    "responses": {
                        "200": {
                            "description": "The caller's documents.",
                            "content": _json("DocumentList"),
                        },
                        "400": OWNER_ERRORS["400"],
                        **COMMON_ERRORS,
                    },
                },
                "post": {
                    "tags": ["Documents"],
                    "summary": "Create a document",
                    "description": (
                        "The server assigns the id. To keep a local draft's id, use "
                        "`PUT /api/documents/{doc_id}/` instead. HTML is sanitised on save."
                    ),
                    "operationId": "createDocument",
                    "security": SECURED,
                    "requestBody": {
                        "required": False,
                        "content": _json(
                            "DocumentInput",
                            {"title": "Quarterly plan", "template": "blank", "html": "<p>Hello</p>"},
                        ),
                    },
                    "responses": {
                        "201": {"description": "Created.", "content": _json("Document")},
                        "400": OWNER_ERRORS["400"],
                        "409": _error("The client has reached the 200 document limit."),
                        "413": _error("Document HTML exceeds 5 MB."),
                        **COMMON_ERRORS,
                    },
                },
            },
            "/api/documents/{doc_id}/": {
                "parameters": [UUID_PARAM_DOC],
                "get": {
                    "tags": ["Documents"],
                    "summary": "Get a document",
                    "operationId": "getDocument",
                    "security": SECURED,
                    "responses": {
                        "200": {"description": "The document.", "content": _json("Document")},
                        "400": OWNER_ERRORS["400"],
                        "404": OWNER_ERRORS["404"],
                        **COMMON_ERRORS,
                    },
                },
                "put": {
                    "tags": ["Documents"],
                    "summary": "Save (create or update) a document",
                    "description": (
                        "Upsert under the given id. If it does not exist it is created (201); "
                        "otherwise only the fields you send are changed (200). This is the call "
                        "the editor uses to sync a local draft. A document id owned by another "
                        "client returns 404."
                    ),
                    "operationId": "saveDocument",
                    "security": SECURED,
                    "requestBody": {
                        "required": True,
                        "content": _json(
                            "DocumentInput", {"title": "Quarterly plan", "html": "<p>Updated</p>"}
                        ),
                    },
                    "responses": {
                        "200": {"description": "Updated.", "content": _json("Document")},
                        "201": {"description": "Created.", "content": _json("Document")},
                        "400": OWNER_ERRORS["400"],
                        "404": OWNER_ERRORS["404"],
                        "409": _error("The client has reached the 200 document limit."),
                        "413": _error("Document HTML exceeds 5 MB."),
                        **COMMON_ERRORS,
                    },
                },
                "delete": {
                    "tags": ["Documents"],
                    "summary": "Delete a document",
                    "description": "Also invalidates its share link.",
                    "operationId": "deleteDocument",
                    "security": SECURED,
                    "responses": {
                        "204": {"description": "Deleted."},
                        "400": OWNER_ERRORS["400"],
                        "404": OWNER_ERRORS["404"],
                        **COMMON_ERRORS,
                    },
                },
            },
            "/api/documents/{doc_id}/share/": {
                "parameters": [UUID_PARAM_DOC],
                "post": {
                    "tags": ["Sharing"],
                    "summary": "Create a share link",
                    "description": "Idempotent: returns the existing token if the document is already shared.",
                    "operationId": "shareDocument",
                    "security": SECURED,
                    "responses": {
                        "200": {
                            "description": "The share token.",
                            "content": _json(
                                "ShareLink",
                                {"shareToken": "Xk2n9vQp0dLw3sT7aBcDeg", "path": "/shared/Xk2n9vQp0dLw3sT7aBcDeg"},
                            ),
                        },
                        "400": OWNER_ERRORS["400"],
                        "404": OWNER_ERRORS["404"],
                        **COMMON_ERRORS,
                    },
                },
                "delete": {
                    "tags": ["Sharing"],
                    "summary": "Revoke the share link",
                    "description": "The old token stops working immediately.",
                    "operationId": "unshareDocument",
                    "security": SECURED,
                    "responses": {
                        "204": {"description": "Link revoked."},
                        "400": OWNER_ERRORS["400"],
                        "404": OWNER_ERRORS["404"],
                        **COMMON_ERRORS,
                    },
                },
            },
            "/api/shared/{token}/": {
                "get": {
                    "tags": ["Sharing"],
                    "summary": "Read a shared document",
                    "description": "Public and read-only. No `X-Client-Id` header needed. The owner's id is never exposed.",
                    "operationId": "getSharedDocument",
                    "parameters": [
                        {
                            "name": "token",
                            "in": "path",
                            "required": True,
                            "description": "Share token from the share endpoint.",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {"description": "The shared document.", "content": _json("SharedDocument")},
                        "404": _error("Unknown or revoked token."),
                        **COMMON_ERRORS,
                    },
                }
            },
            "/api/proofing/spellcheck/": {
                "post": {
                    "tags": ["Proofing"],
                    "summary": "Check spelling",
                    "description": (
                        "Returns known misspellings with character offsets and suggestions "
                        "(capitalisation of the original word is preserved)."
                    ),
                    "operationId": "spellcheck",
                    "requestBody": {
                        "required": True,
                        "content": _json("TextInput", {"text": "I will Recieve it teh day."}),
                    },
                    "responses": {
                        "200": {
                            "description": "Issues found (empty list if none).",
                            "content": _json(
                                "SpellcheckResult",
                                {
                                    "issues": [
                                        {"word": "Recieve", "index": 7, "suggestions": ["Receive", "Relieve", "Recipe"]},
                                        {"word": "teh", "index": 18, "suggestions": ["the", "ten", "tea"]},
                                    ]
                                },
                            ),
                        },
                        "400": _error("`text` is missing or not a string, or the body is invalid JSON."),
                        "413": _error("Text exceeds 200,000 characters."),
                        **COMMON_ERRORS,
                    },
                }
            },
            "/api/proofing/readability/": {
                "post": {
                    "tags": ["Proofing"],
                    "summary": "Readability statistics",
                    "description": "Word, sentence and syllable counts with Flesch scores and estimated reading time.",
                    "operationId": "readability",
                    "requestBody": {
                        "required": True,
                        "content": _json("TextInput", {"text": "The cat sat. The dog ran."}),
                    },
                    "responses": {
                        "200": {"description": "Statistics.", "content": _json("Readability")},
                        "400": _error("`text` is missing or not a string, or the body is invalid JSON."),
                        "413": _error("Text exceeds 200,000 characters."),
                        **COMMON_ERRORS,
                    },
                }
            },
            "/api/proofing/synonyms/": {
                "get": {
                    "tags": ["Proofing"],
                    "summary": "Thesaurus lookup",
                    "operationId": "synonyms",
                    "parameters": [
                        {
                            "name": "word",
                            "in": "query",
                            "required": True,
                            "description": "Word to look up (case-insensitive, 64 characters maximum).",
                            "schema": {"type": "string", "maxLength": 64},
                            "example": "good",
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Synonyms (empty list if the word is unknown).",
                            "content": _json(
                                "SynonymResult",
                                {"word": "good", "synonyms": ["excellent", "fine", "superior", "satisfactory"]},
                            ),
                        },
                        "400": _error("`word` is missing or too long."),
                        **COMMON_ERRORS,
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "ClientId": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Client-Id",
                    "description": (
                        "Anonymous client identity: any random UUID, generated once per browser "
                        "and reused. Example: `3f2b8c1e-7a4d-4e0b-9c55-1a2b3c4d5e6f`."
                    ),
                }
            },
            "schemas": {
                "Error": {
                    "type": "object",
                    "required": ["error"],
                    "properties": {
                        "error": {
                            "type": "object",
                            "required": ["code", "message"],
                            "properties": {
                                "code": {
                                    "type": "string",
                                    "description": "Stable machine-readable code.",
                                    "example": "invalid_client_id",
                                },
                                "message": {"type": "string", "description": "Human-readable explanation."},
                            },
                        }
                    },
                },
                "Health": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "example": "ok"},
                        "service": {"type": "string", "example": "greendoc-api"},
                    },
                },
                "DocumentInput": {
                    "type": "object",
                    "description": "All fields optional. On update, omitted fields are left unchanged.",
                    "properties": {
                        "title": {
                            "type": "string",
                            "maxLength": 200,
                            "description": "Trimmed and cut to 200 characters; blank becomes `Document1`.",
                        },
                        "template": {"type": "string", "maxLength": 64, "example": "blank"},
                        "html": {
                            "type": "string",
                            "description": "Document body. Sanitised on save (scripts, event handlers and unsafe URLs removed). Max 5 MB.",
                        },
                    },
                },
                "DocumentSummary": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "title": {"type": "string"},
                        "template": {"type": "string"},
                        "shared": {"type": "boolean", "description": "Whether a share link is active."},
                        "createdAt": {"type": "string", "format": "date-time"},
                        "updatedAt": {"type": "string", "format": "date-time"},
                    },
                },
                "Document": {
                    "allOf": [
                        {"$ref": "#/components/schemas/DocumentSummary"},
                        {
                            "type": "object",
                            "properties": {
                                "html": {"type": "string", "description": "Sanitised document body."},
                                "shareToken": {"type": "string", "nullable": True},
                            },
                        },
                    ]
                },
                "DocumentList": {
                    "type": "object",
                    "properties": {
                        "documents": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/DocumentSummary"},
                        }
                    },
                },
                "ShareLink": {
                    "type": "object",
                    "properties": {
                        "shareToken": {"type": "string"},
                        "path": {
                            "type": "string",
                            "description": "Suggested frontend route for the public page.",
                        },
                    },
                },
                "SharedDocument": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "template": {"type": "string"},
                        "html": {"type": "string"},
                        "updatedAt": {"type": "string", "format": "date-time"},
                    },
                },
                "TextInput": {
                    "type": "object",
                    "required": ["text"],
                    "properties": {"text": {"type": "string", "maxLength": 200000}},
                },
                "SpellIssue": {
                    "type": "object",
                    "properties": {
                        "word": {"type": "string", "description": "The word as written."},
                        "index": {"type": "integer", "description": "Character offset in the input text."},
                        "suggestions": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "SpellcheckResult": {
                    "type": "object",
                    "properties": {
                        "issues": {"type": "array", "items": {"$ref": "#/components/schemas/SpellIssue"}}
                    },
                },
                "Readability": {
                    "type": "object",
                    "properties": {
                        "words": {"type": "integer", "example": 6},
                        "sentences": {"type": "integer", "example": 2},
                        "syllables": {"type": "integer", "example": 6},
                        "fleschReadingEase": {
                            "type": "number",
                            "nullable": True,
                            "description": "Higher is easier; null for empty text.",
                            "example": 119.2,
                        },
                        "fleschKincaidGrade": {
                            "type": "number",
                            "nullable": True,
                            "description": "US school grade level; null for empty text.",
                            "example": -2.6,
                        },
                        "readingTimeMinutes": {"type": "number", "example": 0.0},
                    },
                },
                "SynonymResult": {
                    "type": "object",
                    "properties": {
                        "word": {"type": "string"},
                        "synonyms": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }


def build_spec(server_url="/"):
    # Imported here: these modules reuse helpers defined above.
    from . import openapi_accounts, openapi_extra

    return openapi_accounts.extend(openapi_extra.extend(_base_spec(server_url)))
