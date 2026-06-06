"""Student and admin analytics helpers."""

from django.db.models import Avg, Count, Q

from .models import Answer, Attempt, Quiz


def student_weak_topics(user, limit=8):
    qs = (
        Answer.objects.filter(attempt__user=user, is_correct=False)
        .exclude(question__topic="")
        .values("question__topic")
        .annotate(wrong_count=Count("id"))
        .order_by("-wrong_count")[:limit]
    )
    return list(qs)


def student_section_performance(user):
    return (
        Attempt.objects.filter(user=user, quiz__section__isnull=False)
        .values("quiz__section")
        .annotate(
            attempts=Count("id"),
            avg_score=Avg("score"),
            avg_marks=Avg("marks_obtained"),
        )
        .order_by("quiz__section")
    )


def admin_placement_stats():
    return {
        "by_company": (
            Attempt.objects.filter(quiz__company__isnull=False)
            .values("quiz__company__name")
            .annotate(attempts=Count("id"), avg_score=Avg("score"))
            .order_by("-attempts")[:10]
        ),
        "by_section": (
            Attempt.objects.values("quiz__section")
            .annotate(attempts=Count("id"), avg_score=Avg("score"))
            .order_by("-attempts")
        ),
        "recent_attempts": (
            Attempt.objects.select_related("user", "quiz", "quiz__company")
            .order_by("-completed_at")[:15]
        ),
    }


def suggested_tests_for_user(user, limit=5):
    weak = [row["question__topic"] for row in student_weak_topics(user, limit=3)]
    qs = Quiz.objects.filter(status="active", company__isnull=False)
    if weak:
        topic_q = Q()
        for t in weak:
            topic_q |= Q(question__topic__icontains=t)
        qs = qs.filter(topic_q).distinct()
    return qs.annotate(q_count=Count("question")).filter(q_count__gt=0)[:limit]
