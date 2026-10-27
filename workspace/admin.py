from django.contrib import admin

from .models import Preferences, Source

admin.site.register([Preferences, Source])
