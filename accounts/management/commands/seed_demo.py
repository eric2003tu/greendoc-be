from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts import services
from accounts.models import Profile

User = get_user_model()


class Command(BaseCommand):
    help = "Create (or reset) the shared demo account shown on the sign-in screen."

    def handle(self, *args, **options):
        email = settings.DEMO_EMAIL
        user = User.objects.filter(username=email).first()
        if user is None:
            services.create_user(email, "Demo", "User", settings.DEMO_PASSWORD)
            self.stdout.write(self.style.SUCCESS(f"Created demo account {email}"))
            return
        user.set_password(settings.DEMO_PASSWORD)
        user.first_name, user.last_name, user.is_active = "Demo", "User", True
        user.save()
        Profile.objects.get_or_create(user=user)
        self.stdout.write(self.style.SUCCESS(f"Reset demo account {email}"))
