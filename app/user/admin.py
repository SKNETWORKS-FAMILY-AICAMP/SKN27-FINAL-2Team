from django.contrib import admin

from .models import EmailVerificationCode, UserAccounts


@admin.register(UserAccounts)
class UserAccountsAdmin(admin.ModelAdmin):
    list_display = ("user_id", "email", "nickname", "status", "is_locked", "created_at")
    search_fields = ("email", "nickname")
    list_filter = ("status", "is_locked")
    readonly_fields = ("password_hash", "created_at", "updated_at", "deleted_at")


@admin.register(EmailVerificationCode)
class EmailVerificationCodeAdmin(admin.ModelAdmin):
    list_display = ("email", "purpose", "code", "is_used", "expires_at", "created_at")
    search_fields = ("email",)
    list_filter = ("purpose", "is_used")
