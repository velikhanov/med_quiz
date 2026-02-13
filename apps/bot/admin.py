from django.contrib import admin

from apps.bot.models import UserAnswer, UserCategoryProgress, TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ("telegram_id", "username")


@admin.register(UserCategoryProgress)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ("user", "category__name", "correct_count", "total_answered", "is_completed")


@admin.register(UserAnswer)
class UserAnswerAdmin(admin.ModelAdmin):
    list_display = ("user", "question__question_number", "selected_option", "is_correct")
