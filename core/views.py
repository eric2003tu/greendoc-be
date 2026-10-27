from django.db import connection

from .http import api_view
from django.http import JsonResponse


@api_view(["GET"])
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok", "service": "greendoc-api"})
