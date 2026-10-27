"""Tiny spec-driven validator for JSON settings blobs (no DRF/pydantic needed)."""

import re

from .http import ApiError

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
LANG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


def _fail(key, why):
    raise ApiError(f"{key}: {why}", code="invalid_field")


def boolean():
    def check(key, v):
        if not isinstance(v, bool):
            _fail(key, "must be true or false.")
        return v

    return check


def integer(lo, hi):
    def check(key, v):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            _fail(key, f"must be an integer between {lo} and {hi}.")
        return v

    return check


def number(lo, hi):
    def check(key, v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            _fail(key, f"must be a number between {lo} and {hi}.")
        return v

    return check


def string(max_len, allow_empty=True, pattern=None):
    def check(key, v):
        if not isinstance(v, str) or len(v) > max_len or (not allow_empty and not v.strip()):
            _fail(key, f"must be a string of at most {max_len} characters.")
        if pattern and v and not pattern.match(v):
            _fail(key, "has an invalid format.")
        return v.strip() if not allow_empty else v

    return check


def choice(options):
    options = list(options)

    def check(key, v):
        if v not in options:
            _fail(key, f"must be one of: {', '.join(map(str, options))}.")
        return v

    return check


def margins():
    side = integer(0, 400)

    def check(key, v):
        if not isinstance(v, dict) or set(v) != {"top", "right", "bottom", "left"}:
            _fail(key, "must be an object with top, right, bottom and left.")
        return {k: side(f"{key}.{k}", v[k]) for k in ("top", "right", "bottom", "left")}

    return check


def validate_blob(data, spec, label="settings"):
    """Return only the validated keys; unknown keys are rejected."""
    if not isinstance(data, dict):
        raise ApiError(f"{label} must be an object.", code="invalid_field")
    unknown = sorted(set(data) - set(spec))
    if unknown:
        raise ApiError(f"Unknown {label} field(s): {', '.join(unknown)}.", code="unknown_field")
    return {k: spec[k](k, v) for k, v in data.items()}
