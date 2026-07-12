"""
One-time cleanup: strip leading "Q1.", "Q17." style prefixes from existing
question text. Questions are shuffled at quiz start, so stored numbers are
misleading. Safe to run multiple times (idempotent).

Usage:
    python manage.py clean_question_numbers
"""
import re

from django.core.management.base import BaseCommand

from app1.models import Question

_PREFIX = re.compile(r"^\s*Q\d+\.\s*", flags=re.IGNORECASE)


class Command(BaseCommand):
    help = "Strip 'Q17.' style number prefixes from existing question text."

    def handle(self, *args, **options):
        updated = 0
        for q in Question.objects.all().iterator():
            new_text = _PREFIX.sub("", q.text or "")
            if new_text != q.text:
                q.text = new_text
                q.save(update_fields=["text"])
                updated += 1
        self.stdout.write(self.style.SUCCESS(
            f"Cleaned number prefixes from {updated} question(s)."
        ))
