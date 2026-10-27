from html import escape

from django.http import JsonResponse

from core.http import ApiError, api_view, parse_json

from documents.services import document_stats

from . import services

MAX_TEXT = 200_000


def _text(request):
    text = parse_json(request).get("text")
    if not isinstance(text, str):
        raise ApiError("text must be a string.", code="invalid_text")
    if len(text) > MAX_TEXT:
        raise ApiError("Text is too long (200,000 characters maximum).", status=413, code="too_large")
    return text


@api_view(["POST"])
def spellcheck(request):
    return JsonResponse({"issues": services.find_spelling_issues(_text(request))})


@api_view(["POST"])
def readability(request):
    return JsonResponse(services.readability(_text(request)))


@api_view(["POST"])
def stats(request):
    """Word count for raw text or editor HTML (Review > Word Count)."""
    data = parse_json(request)
    if "html" in data:
        html = data["html"]
        if not isinstance(html, str):
            raise ApiError("html must be a string.", code="invalid_html")
    else:
        text = data.get("text")
        if not isinstance(text, str):
            raise ApiError("Send either html or text as a string.", code="invalid_text")
        html = "".join(f"<p>{escape(line, quote=False)}</p>" for line in text.splitlines())
    if len(html) > MAX_TEXT * 5:
        raise ApiError("Input is too long.", status=413, code="too_large")
    return JsonResponse(document_stats(html))


@api_view(["GET"])
def synonyms(request):
    word = request.GET.get("word", "").strip()
    if not word or len(word) > 64:
        raise ApiError("word query parameter is required (64 characters maximum).", code="invalid_word")
    return JsonResponse({"word": word, "synonyms": services.synonyms_for(word)})
