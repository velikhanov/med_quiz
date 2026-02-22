from typing import Any
from django.db import models


class SystemConfig(models.Model):
    """
    Singleton model to store global configuration (e.g., GitHub Cron Status).
    """
    is_cron_active = models.BooleanField(default=False, help_text="Current status of the GitHub Action Cron")

    class Meta:
        verbose_name = "System Configuration"
        verbose_name_plural = "System Configuration"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> None:
        pass  # Prevent deletion

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self) -> str:
        return "System Configuration"
