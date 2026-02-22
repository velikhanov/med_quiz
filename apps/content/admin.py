from django.contrib import admin
from django.contrib import messages
from django.http import HttpRequest

from apps.content.models import Test, Category, PDFUpload, Question, SystemConfig
from apps.content.services import launch_detached_worker
from apps.content.github_control import enable_cron, disable_cron


@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'is_cron_active')
    readonly_fields = ('is_cron_active',)
    actions = ('manual_enable_cron', 'manual_disable_cron')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="🟢 Enable GitHub Cron")
    def manual_enable_cron(self, request, queryset):
        # Force update DB state to ensure API call happens if out of sync
        queryset.update(is_cron_active=False)
        
        if enable_cron():
            self.message_user(request, "GitHub Cron Enabled.", level=messages.SUCCESS)
        else:
            self.message_user(request, "Failed to enable GitHub Cron.", level=messages.ERROR)

    @admin.action(description="🔴 Disable GitHub Cron")
    def manual_disable_cron(self, request, queryset):
        # Force update DB state to ensure API call happens if out of sync
        queryset.update(is_cron_active=True)
        
        if disable_cron():
            self.message_user(request, "GitHub Cron Disabled.", level=messages.SUCCESS)
        else:
            self.message_user(request, "Failed to disable GitHub Cron.", level=messages.ERROR)


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ('name',)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'test')
    list_filter = ('test',)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('question_with_page', 'subcategory', 'short_text', 'category', 'correct_option')
    list_filter = ('category', 'category__test', 'subcategory')
    search_fields = ('text', 'subcategory', 'question_number')
    ordering = ('-page_number', '-question_number', '-id')

    @admin.display(description='Question (Page)', ordering='-page_number')
    def question_with_page(self, obj: Question) -> str:
        q_num = obj.question_number if obj.question_number is not None else '?'
        p_num = obj.page_number if obj.page_number is not None else '?'
        return f"{q_num} ({p_num})"

    def short_text(self, obj: Question) -> str:
        return f"{obj.text[:50]}..."


@admin.register(PDFUpload)
class PDFUploadAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'file_completion_status', 'is_processing', 'last_processed_page', 'total_pages')
    readonly_fields = ('current_subcategory', 'total_pages', 'incomplete_question_data')
    actions = ('process_batch_5', 'process_batch_10', 'reset_pdf_status')

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)

        if not request.user.is_superuser:
            fields += ('is_processing', 'last_processed_page')

            if obj and obj.last_processed_page > 0:
                fields += ('file',)

        return fields

    def get_actions(self, request):
        actions = super().get_actions(request)

        if not request.user.is_superuser:
            for action in ('process_batch_5', 'process_batch_10', 'reset_pdf_status'):
                actions.pop(action, None)

        return actions

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True

        if obj and obj.is_locked():
            return False

        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True

        if obj and obj.is_locked():
            return False

        if obj and obj.last_processed_page > 0:
            return False

        return super().has_delete_permission(request, obj)

    @admin.display(description="Status")
    def file_completion_status(self, obj: PDFUpload) -> str:
        if obj and obj.last_processed_page == obj.total_pages:
            return "✅"

        if obj.total_pages > 0:
            percent = int((obj.last_processed_page / obj.total_pages) * 100)
            return f"⏳ {percent}%"

        return "⏳ 0%"

    def _process_batch(self, request: HttpRequest, queryset, batch_size: int) -> None:
        valid_ids = list(queryset.filter(is_processing=False).values_list('id', flat=True))
        all_selected_ids = list(queryset.values_list('id', flat=True))
        busy_ids = list(set(all_selected_ids) - set(valid_ids))

        if valid_ids:
            # Lock them immediately
            PDFUpload.objects.filter(id__in=valid_ids).update(is_processing=True)

            launch_detached_worker(pdf_ids=valid_ids, batch_size=batch_size)

            self.message_user(request, f"🚀 OS Background Worker started for {len(valid_ids)} PDFs (Batch {batch_size}).", level=messages.INFO)

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
