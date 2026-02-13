from django.contrib import admin

from apps.content.models import Test, Category, PDFUpload, Question
from django.contrib import messages


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ('name',)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'test')
    list_filter = ('test',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('subcategory', 'short_text', 'category', 'correct_option')
    list_filter = ('category', 'category__test')
    search_fields = ('text',)
    ordering = ('category', 'question_number')

    def short_text(self, obj):
        return f"{obj.text[:50]}..."


@admin.register(PDFUpload)
class PDFUploadAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'file_completion_status', 'is_processing', 'last_processed_page', 'total_pages')
    readonly_fields = ('total_pages', 'is_processing', 'last_processed_page', 'incomplete_question_data')
    actions = ('process_next_chunk_async',)

    def has_change_permission(self, request, obj=None):
        if obj and obj.is_locked():
            return False

        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.is_locked():
            return False

        return super().has_delete_permission(request, obj)

    @admin.display(description="Status")
    def file_completion_status(self, obj):
        if obj and obj.is_locked():
            return "✅"

        if obj.total_pages > 0:
            percent = int((obj.last_processed_page / obj.total_pages) * 100)
            return f"⏳ {percent}%"
        return "⏳ 0%"

    @admin.action(description="⚡ Process Next Batch (10 Pages, Background)")
    def process_next_chunk_async(self, request, queryset):
        import threading
        from apps.content.services import background_worker

        # 1. OPTIMIZED FILTER (1 Query)
        # Ask DB for IDs that are NOT processing.
        # This replaces the loop and the .refresh_from_db() calls.
        valid_ids = list(queryset.filter(is_processing=False).values_list('id', flat=True))

        # Calculate busy IDs purely in Python (0 Queries)
        all_selected_ids = list(queryset.values_list('id', flat=True))
        busy_ids = list(set(all_selected_ids) - set(valid_ids))

        # 2. LOCK VALID ONES (1 Query)
        if valid_ids:
            # Mark them busy immediately so UI updates
            PDFUpload.objects.filter(id__in=valid_ids).update(is_processing=True)

            # 3. START THREAD
            t = threading.Thread(
                target=background_worker,
                args=(valid_ids, 10),
                daemon=True
            )
            t.start()

            self.message_user(request, f"🚀 Queue started for {len(valid_ids)} PDFs.", level=messages.INFO)

        if busy_ids:
            self.message_user(request, f"⚠️ Skipped {len(busy_ids)} busy PDFs.", level=messages.WARNING)

    @admin.action(description="🔥 Reset Status & Delete Questions")
    def reset_pdf_status(self, request, queryset):
        from apps.content.models import Question

        # 1. Get the list of Category IDs from the selected PDFs
        # We use 'category_id' to get the raw integer directly
        category_ids = queryset.values_list('category_id', flat=True)

        # 2. Delete ALL questions that belong to these Categories
        # WARNING: If you have multiple PDFs linked to the SAME category (e.g. Part 1 & Part 2),
        # this will delete questions for BOTH files.
        deleted_count, _ = Question.objects.filter(category_id__in=category_ids).delete()

        # 3. Reset the PDF variables
        queryset.update(
            last_processed_page=0,
            is_processing=False,
            incomplete_question_data=None  # Clear the buffer so we don't start with junk
        )

        self.message_user(
            request,
            f"♻️ Reset {queryset.count()} PDFs and deleted {deleted_count} questions.",
            level=messages.SUCCESS
        )

    # Add to your actions list
    actions = ['process_next_chunk_async', 'reset_pdf_status']
