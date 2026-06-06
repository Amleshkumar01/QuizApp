from django.contrib import admin

from .models import Answer, Attempt, Category, Company, Option, Question, Quiz


class OptionInline(admin.TabularInline):
    model = Option
    extra = 2
    max_num = 4


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("text", "quiz", "topic", "ai_generated")
    inlines = [OptionInline]


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "section", "difficulty", "category", "status", "created_at")
    list_filter = ("company", "section", "difficulty", "status")


@admin.register(Attempt)
class AttemptAdmin(admin.ModelAdmin):
    list_display = ("user", "quiz", "score", "total", "marks_obtained", "completed_at")
    list_filter = ("quiz", "user")
    search_fields = ("user__username", "quiz__title")


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ("attempt", "question", "selected_option", "is_correct")


admin.site.register(Category)
