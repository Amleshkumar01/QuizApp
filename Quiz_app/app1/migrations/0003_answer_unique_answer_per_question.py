# Generated manually for Answer uniqueness constraint

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app1", "0002_category_question_attempt_option_answer_quiz_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="answer",
            constraint=models.UniqueConstraint(
                fields=("attempt", "question"),
                name="unique_answer_per_question",
            ),
        ),
    ]
