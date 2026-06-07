"""Debug college authentication and student data extraction."""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from app1.college_auth import verify_college_login


class Command(BaseCommand):
    help = "Test college authentication and debug student data extraction"

    def add_arguments(self, parser):
        parser.add_argument("enrollment_id", type=str, help="Student enrollment ID")
        parser.add_argument("password", type=str, help="Student password")

    def handle(self, *args, **options):
        enrollment_id = options["enrollment_id"]
        password = options["password"]

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write("Testing College Authentication")
        self.stdout.write(f"{'='*60}\n")

        result = verify_college_login(enrollment_id, password)

        self.stdout.write(f"Success: {result.success}")
        if not result.success:
            self.stdout.write(self.style.ERROR(f"Error: {result.error_message}"))
            return

        self.stdout.write(self.style.SUCCESS("\nExtracted Data:"))
        self.stdout.write(f"  Enrollment ID: {result.enrollment_id}")
        self.stdout.write(f"  Display Name: {result.display_name or '(empty - will use enrollment ID)'}")
        self.stdout.write(f"  Email: {result.email or '(empty)'}")

        user, created = User.objects.get_or_create(
            username=result.enrollment_id,
            defaults={
                "email": result.email or "",
                "first_name": result.display_name or "",
                "is_staff": False,
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"\nCreated new user: {user.username}"))
        else:
            self.stdout.write(f"\nExisting user: {user.username}")

        self.stdout.write(f"  User Email: {user.email}")
        self.stdout.write(f"  User First Name: {user.first_name}")
        self.stdout.write(f"{'='*60}\n")
