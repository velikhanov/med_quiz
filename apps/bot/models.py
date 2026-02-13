from django.db import models
from apps.content.models import Question, Category


class TelegramUser(models.Model):
    """Stores student identity."""
    telegram_id = models.BigIntegerField(unique=True, db_index=True)
    username = models.CharField(max_length=255, null=True, blank=True)
    first_name = models.CharField(max_length=255, null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.first_name or 'User'} ({self.telegram_id})"


class UserCategoryProgress(models.Model):
    """Tracks progress (e.g., 'Math: 5/20')"""
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)

    correct_count = models.IntegerField(default=0)
    total_answered = models.IntegerField(default=0)
    is_completed = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'category')

    def reset_progress(self):
        """Wipes data for this category"""
        self.correct_count = 0
        self.total_answered = 0
        self.is_completed = False
        self.save()
        # Delete detailed logs
        UserAnswer.objects.filter(user=self.user, question__category=self.category).delete()

    def __str__(self):
        return f"{self.user} - {self.category}"


class UserAnswer(models.Model):
    """Tracks every single click (History Log)"""
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='attempts')
    question = models.ForeignKey(Question, on_delete=models.CASCADE)

    selected_option = models.CharField(max_length=1)
    is_correct = models.BooleanField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'question')  # One answer per question
        indexes = [
            models.Index(fields=['user', 'is_correct']),
        ]

    def __str__(self):
        return f"{self.user} - Q{self.question.id}"
