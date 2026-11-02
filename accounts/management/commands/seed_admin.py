import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts import services
from accounts.models import Profile

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Create (or reset) an administrator who can sign in to /admin/ and to the app. "
        "Safe to run again: it resets the password and name. The password comes from --password or "
        "the GREENDOC_ADMIN_PASSWORD environment variable, so it never has to be written in a file."
    )

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--first-name", required=True)
        parser.add_argument("--last-name", required=True)
        parser.add_argument("--password", default=os.environ.get("GREENDOC_ADMIN_PASSWORD"))

    def handle(self, *args, **options):
        password = options["password"]
        if not password:
            raise CommandError("Give --password or set GREENDOC_ADMIN_PASSWORD.")
        email = services.clean_email(options["email"])
        first = services.clean_name(options["first_name"], "firstName", "first name")
        last = services.clean_name(options["last_name"], "lastName", "last name")

        # The password is deliberately not run through the sign-up strength rules: an
        # administrator chooses their own. Use a strong one anywhere other than a local machine.
        user = User.objects.filter(username=email).first()
        created = user is None
        if created:
            user = services.create_user(email, first, last, password)
        else:
            user.set_password(password)
            user.first_name, user.last_name = first, last
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save()
        Profile.objects.get_or_create(user=user)
        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} administrator {first} {last} <{email}>"))
