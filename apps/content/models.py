from django.db import models


class Test(models.Model):
    """Level 1: The Main Book (e.g., 'DAHİLİYE', 'PEDİATRİ')"""
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name


class Category(models.Model):
    """Level 2: The Chapter/File (e.g., 'HEMATOLOJİ', 'KARDİYOLOJİ')"""
    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=255)

    class Meta:
        unique_together = ('test', 'name')
        verbose_name_plural = "Categories"

    def __str__(self):
        return f"{self.name} ({self.test.name})"


class PDFUpload(models.Model):
    """You upload 'hematoloji.pdf' and link it to the Hematoloji Category"""
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    file = models.FileField()
    title = models.CharField(max_length=255)

    current_subcategory = models.CharField(
        max_length=255, default="Genel",
        help_text="The last detected subcategory (e.g. 'Anemiler'). Used for continuity."
    )

    incomplete_question_data = models.JSONField(
        null=True, blank=True,
        help_text="Temporary buffer for questions split across pages"
    )

    # Progress
    is_processing = models.BooleanField(default=False, help_text="True if background task is running")
    total_pages = models.IntegerField(default=0)
    last_processed_page = models.IntegerField(default=0)

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if self.file and self.total_pages == 0:
            try:
                import fitz

                self.file.open(mode='rb')
                file_data = self.file.read()

                with fitz.open(stream=file_data, filetype="pdf") as doc:
                    self.total_pages = len(doc)

                self.file.seek(0)
            except Exception as e:
                print(f"Error counting pages: {e}")

        super().save(*args, **kwargs)

        if is_new and not self.is_processing:
            import threading
            from .github_control import enable_cron

            print("Pg Up: New file detected. Enabling GitHub Cron...")
            t = threading.Thread(target=enable_cron)
            t.daemon = True
            t.start()

    def delete(self, *args, **kwargs):
        # 1. Delete the file from disk
        if self.file:
            import os

            if os.path.isfile(self.file.path):
                os.remove(self.file.path)

        # 2. Call the standard delete logic
        super().delete(*args, **kwargs)

    def is_locked(self) -> bool:
        """
        Returns True if the file should be read-only.
        Locked if:
        1. It is currently processing (background task running).
        2. It is fully completed (all pages done).
        """
        is_finished = (self.total_pages > 0 and self.last_processed_page >= self.total_pages)
        return self.is_processing or is_finished

    def __str__(self):
        return self.title


class Question(models.Model):
    """Level 3: The Content"""
    question_number = models.IntegerField(null=True, blank=True, help_text="The number from the original book")

    category = models.ForeignKey(Category, on_delete=models.CASCADE)

    # AI finds this (e.g., "Anemiler", "Lösemiler")
    subcategory = models.CharField(max_length=255, blank=True, null=True, default="Genel")

    text = models.TextField()
    options = models.JSONField()
    correct_option = models.CharField(max_length=1)
    explanation = models.TextField(blank=True, null=True)
    page_number = models.IntegerField(help_text="The page number in the PDF")

    class Meta:
        indexes = [
            models.Index(fields=['category', 'page_number', 'question_number', 'id']),
        ]

    def __str__(self):
        return f"{self.text[:50]}..."
