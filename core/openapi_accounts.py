"""OpenAPI descriptions for accounts, sharing/collaboration and support."""

from .openapi import COMMON_ERRORS, _error, _json
from .openapi_extra import BOOL, DATE, INT, STR, STRS, UUID_PARAM_DOC, ref, s_obj

BEARER = [{"BearerAuth": []}]
ERR = {
    "400": "Invalid request. `error.fields` names the fields to correct.",
    "401": "Not signed in, or the session has expired.",
    "403": "Not allowed (wrong password, view-only link, or protected document).",
    "404": "Not found.",
    "409": "Conflict (for example the email is already registered).",
    "429": "Too many attempts or emails; try again later.",
}


def op(tag, summary, operation_id, description="", ok=None, errors=(), body=None, body_example=None,
       ok_code="200", ok_description="Success.", auth=None, params=None):
    operation = {"tags": [tag], "summary": summary, "operationId": operation_id}
    if description:
        operation["description"] = description
    if auth is not None:
        operation["security"] = auth
    if params:
        operation["parameters"] = params
    if body:
        operation["requestBody"] = {"required": True, "content": _json(body, body_example)}
    responses = {}
    if ok:
        responses[ok_code] = {"description": ok_description, "content": _json(ok)}
    else:
        responses[ok_code] = {"description": ok_description}
    for code in errors:
        responses[code] = _error(ERR[code])
    responses.setdefault("429", COMMON_ERRORS["429"])
    operation["responses"] = responses
    return operation


def build_paths():
    d = [UUID_PARAM_DOC]
    guest_note = (
        " Send your guest `X-Client-Id` header too and the documents, sources and settings you made "
        "as a guest move into the account (set `claimGuestData` to false to skip)."
    )
    return {
        "/api/auth/register/": {
            "post": op("Account", "Create an account", "register",
                       "Just email, first name, last name and password. Signs you in straight away." + guest_note,
                       body="RegisterInput", ok="AuthResult", ok_code="201", ok_description="Account created and signed in.",
                       errors=("400", "409"),
                       body_example={"email": "amina@example.com", "firstName": "Amina", "lastName": "Ndlovu",
                                     "password": "a-long-passphrase"}),
        },
        "/api/auth/login/": {
            "post": op("Account", "Sign in", "login",
                       "Returns a session token to send as `Authorization: Bearer <token>`. After 5 wrong "
                       "passwords the email is locked for 15 minutes. To try it quickly, use the shared demo account "
                       "`demo@greendoc.app` / `GreenDoc-demo-2026` (run `python manage.py seed_demo` first)." + guest_note,
                       body="LoginInput", ok="AuthResult", errors=("400", "401", "429"),
                       body_example={"email": "amina@example.com", "password": "a-long-passphrase"}),
        },
        "/api/auth/logout/": {
            "post": op("Account", "Sign out", "logout", "Ends this device's session.", auth=BEARER,
                       ok_code="204", ok_description="Signed out.", errors=("401",)),
        },
        "/api/auth/logout-all/": {
            "post": op("Account", "Sign out everywhere", "logoutAll", auth=BEARER,
                       ok_code="204", ok_description="All sessions ended.", errors=("401",)),
        },
        "/api/auth/me/": {
            "get": op("Account", "My account", "getAccount",
                      "File > Account: name, email and how many documents are saved.",
                      auth=BEARER, ok="User", errors=("401",)),
            "put": op("Account", "Edit my profile", "updateAccount",
                      "Change first/last name freely. Changing the email also needs `currentPassword`.",
                      auth=BEARER, body="ProfileUpdate", ok="User", errors=("400", "401", "403", "409"),
                      body_example={"firstName": "Amina", "lastName": "Dlamini"}),
            "delete": op("Account", "Delete my account", "deleteAccount",
                         "Permanently deletes the account and all its documents, comments, versions and settings. Needs the password.",
                         auth=BEARER, body="PasswordConfirm", ok_code="204", ok_description="Account deleted.",
                         errors=("401", "403")),
        },
        "/api/auth/change-password/": {
            "post": op("Account", "Change password", "changePassword",
                       "Other devices are signed out; this one stays signed in.",
                       auth=BEARER, body="ChangePasswordInput", ok="Message", errors=("400", "401", "403"),
                       body_example={"currentPassword": "old-passphrase", "newPassword": "a-new-long-passphrase"}),
        },
        "/api/auth/forgot-password/": {
            "post": op("Account", "Email a password reset link", "forgotPassword",
                       "Always replies the same way, whether or not the email has an account.",
                       body="ForgotInput", ok="Message", errors=("400",), body_example={"email": "amina@example.com"}),
        },
        "/api/auth/reset-password/": {
            "post": op("Account", "Set a new password from the email link", "resetPassword",
                       "Use the `uid` and `token` from the link. Signs out every device.",
                       body="ResetInput", ok="Message", errors=("400",)),
        },
        "/api/documents/{doc_id}/invite/": {
            "parameters": d,
            "post": op("Sharing", "Invite people by email", "inviteToDocument",
                       "Share > Collaborate. Emails the document's link to up to 10 people with `edit` (default) "
                       "or `view` permission, creating the link if needed. Requires sign-in; 20 emails per hour.",
                       auth=BEARER, body="InviteInput", ok="InviteResult", errors=("400", "401", "404", "429"),
                       body_example={"emails": ["morgan@example.com"], "permission": "edit", "message": "Please review."}),
        },
        "/api/documents/{doc_id}/email-copy/": {
            "parameters": d,
            "post": op("Sharing", "Email a copy", "emailDocumentCopy",
                       "Share > Email a Copy. Sends the document as an attachment (default .docx) to yourself or another address. Requires sign-in.",
                       auth=BEARER, body="EmailCopyInput", ok="EmailCopyResult", errors=("400", "401", "404", "429"),
                       body_example={"format": "docx"}),
        },
        "/api/support/": {
            "post": op("Support", "Contact support", "contactSupport",
                       "Help > Contact Support. Open to everyone; signed-in people can leave out name and email.",
                       body="SupportInput", ok="SupportReceipt", ok_code="201", ok_description="Message received.",
                       errors=("400", "429"),
                       body_example={"email": "amina@example.com", "subject": "Can't export", "message": "The export button does nothing."}),
        },
    }


def build_schemas():
    pw = {"type": "string", "minLength": 8, "maxLength": 128, "format": "password"}
    return {
        "RegisterInput": s_obj(
            {"email": {"type": "string", "format": "email"}, "firstName": {"type": "string", "maxLength": 50},
             "lastName": {"type": "string", "maxLength": 50},
             "password": {**pw, "description": "At least 8 characters; not too common, not all digits, not like your name or email."},
             "claimGuestData": {"type": "boolean", "default": True, "description": "Move this browser's guest documents into the new account."}},
            required=["email", "firstName", "lastName", "password"]),
        "LoginInput": s_obj(
            {"email": {"type": "string", "format": "email"}, "password": {"type": "string", "format": "password"},
             "claimGuestData": {"type": "boolean", "default": True}},
            required=["email", "password"]),
        "User": s_obj({"id": STR, "email": {"type": "string", "format": "email"}, "firstName": STR, "lastName": STR,
                       "displayName": {**STR, "example": "Amina Ndlovu"}, "initials": {**STR, "example": "AN"},
                       "createdAt": DATE, "documentCount": INT}),
        "AuthResult": s_obj(
            {"token": {**STR, "description": "Send as `Authorization: Bearer <token>`. Valid for 30 days; shown only once."},
             "user": ref("User"),
             "claimed": s_obj({"documents": INT, "sources": INT, "preferences": BOOL},
                              description="What moved from the guest session into the account (present when a guest id was sent).")},
            required=["token", "user"]),
        "ProfileUpdate": s_obj({"firstName": {"type": "string", "maxLength": 50}, "lastName": {"type": "string", "maxLength": 50},
                                "email": {"type": "string", "format": "email"},
                                "currentPassword": {"type": "string", "format": "password", "description": "Required when changing the email."}}),
        "PasswordConfirm": s_obj({"password": {"type": "string", "format": "password"}}, required=["password"]),
        "ChangePasswordInput": s_obj({"currentPassword": {"type": "string", "format": "password"}, "newPassword": pw},
                                     required=["currentPassword", "newPassword"]),
        "ForgotInput": s_obj({"email": {"type": "string", "format": "email"}}, required=["email"]),
        "ResetInput": s_obj({"uid": STR, "token": STR, "newPassword": pw}, required=["uid", "token", "newPassword"]),
        "Message": s_obj({"message": STR}),
        "InviteInput": s_obj({"emails": {"type": "array", "items": {"type": "string", "format": "email"}, "minItems": 1, "maxItems": 10},
                              "permission": {"type": "string", "enum": ["view", "edit"], "default": "edit"},
                              "message": {"type": "string", "maxLength": 1000}}, required=["emails"]),
        "InviteResult": s_obj({"sent": INT, "shareToken": STR, "permission": {"type": "string", "enum": ["view", "edit"]},
                               "path": STR}),
        "EmailCopyInput": s_obj({"to": {"type": "string", "format": "email", "description": "Defaults to your own email."},
                                 "format": {"type": "string", "enum": ["docx", "html", "txt", "rtf", "xml"], "default": "docx"},
                                 "message": {"type": "string", "maxLength": 1000}}),
        "EmailCopyResult": s_obj({"sent": BOOL, "to": STR, "format": STR}),
        "SupportInput": s_obj({"name": {"type": "string", "maxLength": 100}, "email": {"type": "string", "format": "email"},
                               "subject": {"type": "string", "maxLength": 150}, "message": {"type": "string", "maxLength": 5000}},
                              required=["subject", "message"]),
        "SupportReceipt": s_obj({"id": INT, "message": STR}),
    }


def extend(spec):
    spec["paths"].update(build_paths())
    spec["components"]["schemas"].update(build_schemas())
    spec["components"]["securitySchemes"]["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "description": "Session token from sign-in or registration. Optional: guests use `X-Client-Id` instead. "
        "When both are sent the account is used.",
    }
    schemas = spec["components"]["schemas"]
    schemas["Error"]["properties"]["error"]["properties"]["fields"] = {
        "type": "object",
        "description": "Field-level messages for forms, e.g. `{\"email\": \"Enter a valid email address.\"}`. "
        "Password problems list every unmet rule as an array.",
        "additionalProperties": {"oneOf": [STR, STRS]},
    }
    # Sharing now has permissions; editing through an edit link.
    shared = spec["paths"]["/api/shared/{token}/"]
    shared["get"]["description"] = (
        "Public. No sign-in or `X-Client-Id` needed. The owner's id is never exposed. `canEdit` tells the app whether to open it for editing."
    )
    shared["put"] = op(
        "Sharing", "Edit through an edit link", "editSharedDocument",
        "Only works when the link was shared with `edit` permission (anyone with the link can then change the title and text) "
        "and the document is not protected.",
        body="SharedEdit", ok="SharedDocument", errors=("400", "403", "404"),
        body_example={"html": "<p>Edited by a collaborator</p>"},
        params=shared["get"]["parameters"],
    )
    schemas["SharedEdit"] = s_obj({"title": {"type": "string", "maxLength": 200}, "html": STR})
    schemas["SharedDocument"]["properties"].update(
        permission={"type": "string", "enum": ["view", "edit"]}, canEdit=BOOL
    )
    share = spec["paths"]["/api/documents/{doc_id}/share/"]["post"]
    share["description"] = (
        "Idempotent. Send `permission` (`view` default, or `edit`) to create the link or change its permission."
    )
    share["requestBody"] = {"required": False, "content": _json("ShareInput", {"permission": "edit"})}
    schemas["ShareInput"] = s_obj({"permission": {"type": "string", "enum": ["view", "edit"], "default": "view"}})
    schemas["ShareLink"]["properties"]["permission"] = {"type": "string", "enum": ["view", "edit"]}
    spec["tags"].extend(
        [
            {"name": "Account", "description": "Optional sign-in, like Word: guests can use everything; an account keeps your documents on every device."},
            {"name": "Support", "description": "Contact the GreenDoc team."},
        ]
    )
    return spec
