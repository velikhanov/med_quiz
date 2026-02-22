from django.contrib import admin
from django.contrib import messages
from apps.core.models import SystemConfig
from apps.content.github_control import enable_cron, disable_cron

# --- Custom Admin Sorting Logic ---

from django.contrib.admin import AdminSite


def get_app_list_custom(self, request, app_label=None):
    """
    Custom implementation of get_app_list to sort models based on explicit ordering.
    """
    # Call the original implementation directly to get the initial list
    app_list = AdminSite.get_app_list(self, request, app_label)

    # Define custom order for models within apps
    # Format: 'app_label': ['ModelName1', 'ModelName2', ...]
    ordering = {
        'bot': ['TelegramUser', 'UserCategoryProgress', 'UserAnswer'],
        'content': ['Test', 'Category', 'Question', 'PDFUpload'],
        'core': ['SystemConfig'],
    }

    for app in app_list:
        label = app['app_label']
        if label in ordering:
            # Create a map of model_name -> index
            order_map = {name: i for i, name in enumerate(ordering[label])}

            # Sort the models list
            # We use .get(..., 100) to put unknown models at the end
            app['models'].sort(key=lambda x: order_map.get(x['object_name'], 100))

    return app_list


# --- SystemConfig Admin ---

@admin.register(SystemConfig)
class SystemConfigAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'is_cron_active')
    readonly_fields = ('is_cron_active',)
    actions = ('manual_enable_cron', 'manual_disable_cron')

    def has_add_permission(self, request):
        return SystemConfig.objects.count() == 0

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
