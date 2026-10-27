from django.http import JsonResponse

from core.http import ApiError, api_view

from . import data


@api_view(["GET"])
def template_list(request):
    category = request.GET.get("category", "").strip()
    query = request.GET.get("q", "").strip().lower()
    items = [
        {k: t[k] for k in ("id", "name", "desc", "category")}
        for t in data.templates()
        if (not category or category == "All" or t["category"] == category)
        and (not query or query in t["name"].lower() or query in t["desc"].lower())
    ]
    return JsonResponse({"categories": data.TEMPLATE_CATEGORIES, "templates": items})


@api_view(["GET"])
def template_detail(request, template_id):
    template = data.template_by_id(template_id)
    if not template:
        raise ApiError("Template not found.", status=404, code="not_found")
    return JsonResponse(template)


@api_view(["GET"])
def design(request):
    return JsonResponse(data.design())


@api_view(["GET"])
def page_setup(request):
    return JsonResponse(
        {
            "pageSizes": [{"id": k, **v} for k, v in data.PAGE_SIZES.items()],
            "orientations": ["portrait", "landscape"],
            "marginPresets": data.MARGIN_PRESETS,
            "customMargins": data.CUSTOM_MARGINS,
            "defaultMargins": data.DEFAULT_MARGINS,
            "citationStyles": ["APA", "MLA", "Chicago"],
        }
    )


@api_view(["GET"])
def reference(request):
    return JsonResponse(
        {"fonts": data.FONTS, "fontSizes": data.FONT_SIZES, "shortcuts": data.SHORTCUTS}
    )
