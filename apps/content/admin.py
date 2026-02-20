from django.contrib import admin
from django.contrib import messages
import threading

from apps.content.models import Test, Category, PDFUpload, Question


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ('name',)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'test')
    list_filter = ('test',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('question_number', 'page_number', 'subcategory', 'short_text', 'category', 'correct_option')
    list_filter = ('category', 'category__test')
    search_fields = ('text',)
    ordering = ('page_number', 'question_number', 'id')

    def short_text(self, obj):
        return f"{obj.text[:50]}..."


@admin.register(PDFUpload)
class PDFUploadAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'file_completion_status', 'is_processing', 'last_processed_page', 'total_pages')
    readonly_fields = ('current_subcategory', 'total_pages', 'is_processing', 'last_processed_page', 'incomplete_question_data')
    actions = ('process_batch_5', 'process_batch_10', 'reset_pdf_status', 'unlock_pdf_status')

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
        if obj and obj.last_processed_page == obj.total_pages:
            return "✅"

        if obj.total_pages > 0:
            percent = int((obj.last_processed_page / obj.total_pages) * 100)
            return f"⏳ {percent}%"

        return "⏳ 0%"

    def _process_batch(self, request, queryset, batch_size):
        from apps.content.services import background_worker

        valid_ids = list(queryset.filter(is_processing=False).values_list('id', flat=True))
        all_selected_ids = list(queryset.values_list('id', flat=True))
        busy_ids = list(set(all_selected_ids) - set(valid_ids))

        if valid_ids:
            PDFUpload.objects.filter(id__in=valid_ids).update(is_processing=True)

            t = threading.Thread(
                target=background_worker,
                args=(valid_ids, batch_size),
                daemon=True
            )
            t.start()

            self.message_user(request, f"🚀 Queue started for {len(valid_ids)} PDFs (Batch {batch_size}).", level=messages.INFO)

        if busy_ids:
            self.message_user(request, f"⚠️ Skipped {len(busy_ids)} busy PDFs.", level=messages.WARNING)

    @admin.action(description="⚡ Process Next Batch (5 Pages, Background)")
    def process_batch_5(self, request, queryset):
        self._process_batch(request, queryset, 5)

    @admin.action(description="⚡ Process Next Batch (10 Pages, Background)")
    def process_batch_10(self, request, queryset):
        self._process_batch(request, queryset, 10)

    @admin.action(description="🔥 Reset Status & Delete Questions")
    def reset_pdf_status(self, request, queryset):
        valid_ids = list(queryset.filter(is_processing=False).values_list('id', flat=True))
        all_selected_ids = list(queryset.values_list('id', flat=True))
        busy_ids = list(set(all_selected_ids) - set(valid_ids))

        if valid_ids:
            valid_queryset = queryset.filter(id__in=valid_ids)
            category_ids = valid_queryset.values_list('category_id', flat=True)

            deleted_count, _ = Question.objects.filter(category_id__in=category_ids).delete()

            valid_queryset.update(
                last_processed_page=0,
                is_processing=False,
                incomplete_question_data=None
            )

            self.message_user(
                request,
                f"♻️ Reset {len(valid_ids)} PDFs and deleted {deleted_count} questions.",
                level=messages.SUCCESS
            )

        if busy_ids:
            self.message_user(request, f"⚠️ Skipped {len(busy_ids)} busy PDFs.", level=messages.WARNING)

    @admin.action(description="🔓 Unlock Processing Status")
    def unlock_pdf_status(self, request, queryset):
        rows_updated = queryset.update(is_processing=False)
        self.message_user(request, f"🔓 Unlocked {rows_updated} PDFs.", level=messages.SUCCESS)
