"""Manually set or update student display names."""
import csv
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User


class Command(BaseCommand):
    help = "Set or update student display names (names shown in dashboard)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            type=str,
            help="CSV file with columns: enrollment_id, display_name",
        )
        parser.add_argument(
            "--enrollment-id",
            type=str,
            help="Single enrollment ID to update",
        )
        parser.add_argument(
            "--name",
            type=str,
            help="Display name for the enrollment ID",
        )

    def handle(self, *args, **options):
        if options["csv"]:
            self.handle_csv(options["csv"])
        elif options["enrollment_id"] and options["name"]:
            self.handle_single(options["enrollment_id"], options["name"])
        else:
            self.stdout.write(
                self.style.ERROR(
                    "Provide either --csv or both --enrollment-id and --name"
                )
            )

    def handle_single(self, enrollment_id, display_name):
        """Update a single student's name."""
        try:
            user = User.objects.get(username=enrollment_id)
            user.first_name = display_name
            user.save()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Updated {enrollment_id}: {user.first_name}"
                )
            )
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"User not found: {enrollment_id}")
            )

    def handle_csv(self, csv_path):
        """Update student names from a CSV file."""
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames or not all(
                    col in reader.fieldnames for col in ["enrollment_id", "display_name"]
                ):
                    self.stdout.write(
                        self.style.ERROR(
                            "CSV must have columns: enrollment_id, display_name"
                        )
                    )
                    return

                updated = 0
                failed = 0
                for row in reader:
                    enrollment_id = (row.get("enrollment_id") or "").strip()
                    display_name = (row.get("display_name") or "").strip()

                    if not enrollment_id or not display_name:
                        continue

                    try:
                        user = User.objects.get(username=enrollment_id)
                        user.first_name = display_name
                        user.save()
                        updated += 1
                    except User.DoesNotExist:
                        self.stdout.write(
                            self.style.WARNING(f"User not found: {enrollment_id}")
                        )
                        failed += 1

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Updated {updated} students, {failed} not found"
                    )
                )
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f"File not found: {csv_path}"))
