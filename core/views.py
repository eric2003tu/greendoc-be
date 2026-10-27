from django.db import connection
from django.http import HttpResponse, JsonResponse

from .http import api_view
from .openapi import build_spec

SWAGGER_UI_VERSION = "5.17.14"

DOCS_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GreenDoc API</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@%(v)s/swagger-ui.css">
  <style>
    body { margin: 0; }
    .swagger-ui .topbar { background: #0a4d29; }
    .swagger-ui .info .title, .swagger-ui .opblock-tag { color: #0a4d29; }
    .swagger-ui .btn.authorize { color: #0f6b37; border-color: #0f6b37; }
    .swagger-ui .btn.authorize svg { fill: #0f6b37; }
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@%(v)s/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: "/api/openapi.json",
      dom_id: "#swagger-ui",
      deepLinking: true,
      persistAuthorization: true,
      displayRequestDuration: true,
      tryItOutEnabled: true,
    });
  </script>
</body>
</html>
"""


@api_view(["GET"])
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok", "service": "greendoc-api"})


@api_view(["GET"])
def openapi_schema(request):
    return JsonResponse(build_spec(f"{request.scheme}://{request.get_host()}"))


@api_view(["GET"])
def swagger_ui(request):
    return HttpResponse(DOCS_HTML % {"v": SWAGGER_UI_VERSION}, content_type="text/html")
