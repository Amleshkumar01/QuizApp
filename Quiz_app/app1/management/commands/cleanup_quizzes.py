"""
Clean up old auto-generated "practice" quizzes that were created when students
browsed a company (title pattern: "Company — Section (Level) Practice").

Behaviour (safe by design):
  * default (no flags)  → REPORT only. Nothing is changed.
  * --apply             → delete candidates WITHOUT student results,
                          archive candidates WITH student results (never delete
                          data), keep everything else. Asks for confirmation.
  * --yes               → skip the confirmation prompt.
  * --include-empty     → also treat any quiz with 0 questions and no results as
                          junk (deletes it), even if not a practice quiz.

Genuine teacher/admin quizzes (normal titles) are NEVER touched.

Examples:
    python manage.py cleanup_quizzes
    python manage.py cleanup_quizzes --apply
    python manage.py cleanup_quizzes --apply --yes --include-empty
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from app1.models import Attempt, ImportedResult, Quiz


def _has_results(quiz):
    return (
        Attempt.objects.filter(quiz=quiz).exists()
        or ImportedResult.objects.filter(quiz=quiz).exists()
    )


class Command(BaseCommand):
    help = "Report and clean up old auto-generated 'Practice' quizzes."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Actually delete/archive (default is report only).")
        parser.add_argument("--yes", action="store_true",
                            help="Skip the confirmation prompt.")
        parser.add_argument("--include-empty", action="store_true",
                            help="Also delete any 0-question quiz with no results.")

    def handle(self, *args, **options):
        apply = options["apply"]
        assume_yes = options["yes"]
        include_empty = options["include_empty"]

        # Candidate = auto-generated practice quizzes (title contains ") Practice").
        practice = Quiz.objects.filter(title__contains=") Practice")

        candidates = set(practice.values_list("id", flat=True))

        if include_empty:
            empty = (
                Quiz.objects.annotate(qn=Count("question"))
                .filter(qn=0)
                .values_list("id", flat=True)
            )
            candidates.update(empty)

        cand_qs = Quiz.objects.filter(id__in=candidates).select_related("company")

        to_delete, to_archive = [], []
        for q in cand_qs:
            if _has_results(q):
                if not q.is_archived:
                    to_archive.append(q)
            else:
                to_delete.append(q)

        genuine_count = Quiz.objects.exclude(id__in=candidates).count()

        # ---- Report ----
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Quiz Cleanup Report ==="))
        self.stdout.write(f"Total quizzes in DB      : {Quiz.objects.count()}")
        self.stdout.write(f"Candidate default quizzes: {len(candidates)}")
        self.stdout.write(f"  → will DELETE (no results) : {len(to_delete)}")
        self.stdout.write(f"  → will ARCHIVE (has results): {len(to_archive)}")
        self.stdout.write(f"Genuine quizzes (kept)   : {genuine_count}")

        self.stdout.write("\nPer-company candidate breakdown:")
        by_company = {}
        for q in cand_qs:
            name = q.company.name if q.company else "(no company)"
            by_company.setdefault(name, 0)
            by_company[name] += 1
        for name in sorted(by_company):
            self.stdout.write(f"  {name}: {by_company[name]} default quiz(zes)")

        if not apply:
            self.stdout.write(self.style.WARNING(
                "\nReport only. Re-run with --apply to delete/archive."
            ))
            return

        if not to_delete and not to_archive:
            self.stdout.write(self.style.SUCCESS("\nNothing to clean up."))
            return

        if not assume_yes:
            confirm = input(
                f"\nDelete {len(to_delete)} and archive {len(to_archive)} quizzes? [y/N]: "
            ).strip().lower()
            if confirm != "y":
                self.stdout.write(self.style.WARNING("Aborted. No changes made."))
                return

        deleted = 0
        for q in to_delete:
            q.delete()
            deleted += 1

        archived = 0
        for q in to_archive:
            q.is_archived = True
            q.status = "disabled"
            q.archived_at = timezone.now()
            q.save(update_fields=["is_archived", "status", "archived_at"])
            archived += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Deleted {deleted}, archived {archived}. Kept {genuine_count} genuine quizzes."
        ))
