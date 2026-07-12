"""
Idempotent role setup for PlacementIQ.

Creates the "Teacher" group and grants it a curated, least-privilege set of
model permissions. It deliberately never grants:
  * any permission on auth.User / auth.Group / auth.Permission
    (so teachers cannot delete users or manage roles/permissions), and
  * delete on Company / StudentProfile / ImportedResult / AuditLog
    (historical / protected data).

Safe to run repeatedly.
"""
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from app1.permissions import TEACHER_GROUP


# model_name (lowercase) -> allowed actions
TEACHER_MODEL_PERMS = {
    "company": ["add", "change", "view"],
    "category": ["add", "change", "view"],
    "placementdrive": ["add", "change", "view"],
    "quiz": ["add", "change", "delete", "view"],
    "question": ["add", "change", "delete", "view"],
    "option": ["add", "change", "delete", "view"],
    "attempt": ["view"],
    "answer": ["view"],
    "importedresult": ["add", "view"],
    "importbatch": ["add", "view"],
    "studentprofile": ["add", "change", "view"],
    "pendingstudentprofile": ["add", "change", "view"],
    "auditlog": ["add", "view"],
}


class Command(BaseCommand):
    help = "Create the Teacher group and assign least-privilege permissions (idempotent)."

    def handle(self, *args, **options):
        group, created = Group.objects.get_or_create(name=TEACHER_GROUP)
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created group "{TEACHER_GROUP}".'))
        else:
            self.stdout.write(f'Group "{TEACHER_GROUP}" already exists.')

        wanted = []
        missing_models = []
        for model_name, actions in TEACHER_MODEL_PERMS.items():
            try:
                ct = ContentType.objects.get(app_label="app1", model=model_name)
            except ContentType.DoesNotExist:
                missing_models.append(model_name)
                continue
            for action in actions:
                codename = f"{action}_{model_name}"
                perm = Permission.objects.filter(content_type=ct, codename=codename).first()
                if perm:
                    wanted.append(perm)

        # Replace the group's permission set with exactly the curated list so the
        # command is fully idempotent and self-correcting.
        group.permissions.set(wanted)
        group.save()

        self.stdout.write(self.style.SUCCESS(
            f"Assigned {len(wanted)} permissions to the Teacher group."
        ))
        if missing_models:
            self.stdout.write(self.style.WARNING(
                "Skipped (model not migrated yet): " + ", ".join(missing_models)
            ))
        self.stdout.write(self.style.SUCCESS("Role setup complete."))
