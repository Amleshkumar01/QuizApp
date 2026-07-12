"""
Safely change the Super Admin username and/or password.

Interactive and secret-safe: the password is read with a hidden prompt (getpass)
and is validated with Django's password validators. Nothing is printed or logged.

Usage:
    python manage.py set_superadmin
    python manage.py set_superadmin --current admin        # pick which superuser
    python manage.py set_superadmin --username newname     # skip the username prompt
"""
from getpass import getpass

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Change the Super Admin username and password (interactive, secret-safe)."

    def add_arguments(self, parser):
        parser.add_argument("--current", help="Current username of the superuser to edit.")
        parser.add_argument("--username", help="New username (skips the prompt).")

    def handle(self, *args, **options):
        supers = User.objects.filter(is_superuser=True).order_by("pk")
        if not supers.exists():
            raise CommandError("No superuser exists. Create one with `createsuperuser` first.")

        # Choose which superuser to edit.
        if options.get("current"):
            user = supers.filter(username=options["current"]).first()
            if not user:
                raise CommandError(f'No superuser named "{options["current"]}".')
        elif supers.count() == 1:
            user = supers.first()
        else:
            self.stdout.write("Multiple superusers found:")
            for u in supers:
                self.stdout.write(f"  - {u.username}")
            name = input("Enter the username to edit: ").strip()
            user = supers.filter(username=name).first()
            if not user:
                raise CommandError(f'No superuser named "{name}".')

        self.stdout.write(f'Editing superuser: "{user.username}"')

        # New username (optional).
        new_username = options.get("username")
        if new_username is None:
            new_username = input(f"New username [{user.username}]: ").strip()
        if new_username and new_username != user.username:
            if User.objects.filter(username__iexact=new_username).exclude(pk=user.pk).exists():
                raise CommandError(f'Username "{new_username}" is already taken.')
            user.username = new_username

        # New password (optional; hidden input, validated).
        pw1 = getpass("New password (leave blank to keep current): ")
        if pw1:
            pw2 = getpass("Confirm new password: ")
            if pw1 != pw2:
                raise CommandError("Passwords do not match.")
            try:
                validate_password(pw1, user=user)
            except ValidationError as exc:
                raise CommandError("Password rejected: " + " ".join(exc.messages))
            user.set_password(pw1)

        # Guarantee full Super Admin status.
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.save()

        self.stdout.write(self.style.SUCCESS(
            f'Super Admin updated. Username is now "{user.username}". '
            "Log in at /admin/login/."
        ))
