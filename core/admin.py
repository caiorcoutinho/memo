from django.contrib import admin

from .models import Memo, Photo


@admin.register(Memo)
class MemoAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'created_at')
    list_filter = ('owner',)
    search_fields = ('name', 'description')
    filter_horizontal = ('shared_view', 'shared_edit')


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ('name', 'memo', 'owner', 'taken_at', 'uploaded_at')
    list_filter = ('owner', 'memo')
    search_fields = ('name', 'description')
    date_hierarchy = 'taken_at'
    filter_horizontal = ('shared_with',)
