import getpass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create (or reset the password of) the root superuser root@iglweb.com."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Password for the root user. If omitted you will be prompted.")

    def handle(self, *args, **options):
        email = settings.ROOT_EMAIL
        password = options.get("password")
        if not password:
            password = getpass.getpass(f"Password for {email}: ")
            if password != getpass.getpass("Password (again): "):
                raise CommandError("Passwords do not match.")
        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages))

        User = get_user_model()
        user, created = User.objects.get_or_create(username=email, defaults={"email": email})
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()
        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} superuser {email}"))
