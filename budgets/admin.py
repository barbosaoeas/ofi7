from django.contrib import admin

try:
    from . import models
except Exception:
    models = None

if models:
    for name in (
        'WhatsAppFinanceQueueItem',
        'WhatsAppWebhookLog',
        'WhatsAppFinanceQueueAttachment',
    ):
        if hasattr(models, name):
            try:
                admin.site.register(getattr(models, name))
            except admin.sites.AlreadyRegistered:
                pass
