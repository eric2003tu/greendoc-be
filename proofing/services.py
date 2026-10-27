"""Proofing logic. Kept free of Django so it can later be swapped for (or
extended with) an ML-backed implementation without touching the views."""

import json
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
WORD_RE = re.compile(r"[A-Za-z']+")


@lru_cache(maxsize=1)
def _misspellings():
    return json.loads((DATA_DIR / "misspellings.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _synonyms():
    return json.loads((DATA_DIR / "synonyms.json").read_text(encoding="utf-8"))


def _match_case(original, suggestion):
    if original.isupper() and len(original) > 1:
        return suggestion.upper()
    if original[:1].isupper():
        return suggestion[:1].upper() + suggestion[1:]
    return suggestion


def find_spelling_issues(text):
    table = _misspellings()
    issues = []
    for match in WORD_RE.finditer(text):
        word = match.group(0).strip("'")
        suggestions = table.get(word.lower())
        if suggestions:
            issues.append(
                {
                    "word": word,
                    "index": match.start(),
                    "suggestions": [_match_case(word, s) for s in suggestions],
                }
            )
    return issues


def synonyms_for(word):
    return _synonyms().get(word.strip().lower(), [])


def _syllables(word):
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    if word.endswith("e") and not word.endswith("le") and count > 1:
        count -= 1
    return max(count, 1)


def readability(text):
    words = WORD_RE.findall(text)
    sentences = [s for s in re.split(r"[.!?]+(?:\s|$)", text) if s.strip()]
    word_count = len(words)
    sentence_count = max(len(sentences), 1) if word_count else 0
    syllables = sum(_syllables(w) for w in words)
    if not word_count:
        return {
            "words": 0,
            "sentences": 0,
            "syllables": 0,
            "fleschReadingEase": None,
            "fleschKincaidGrade": None,
            "readingTimeMinutes": 0,
        }
    wps = word_count / sentence_count
    spw = syllables / word_count
    return {
        "words": word_count,
        "sentences": sentence_count,
        "syllables": syllables,
        "fleschReadingEase": round(206.835 - 1.015 * wps - 84.6 * spw, 1),
        "fleschKincaidGrade": round(0.39 * wps + 11.8 * spw - 15.59, 1),
        "readingTimeMinutes": round(word_count / 238, 1),
    }
