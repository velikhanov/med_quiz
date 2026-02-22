from django.apps import AppConfig
from django.contrib import admin


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'

    def ready(self):
        # Monkey patch the default AdminSite.get_app_list to support custom sorting
        from .admin import get_app_list_custom

        # We bind the method to the instance `admin.site`
        admin.site.get_app_list = get_app_list_custom.__get__(admin.site, type(admin.site))
