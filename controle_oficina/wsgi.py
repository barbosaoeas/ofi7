"""
WSGI config for controle_oficina project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'controle_oficina.settings')

os.environ["ZAP_WEBHOOK_SECRET"]="tk_18605d1680141f4301d8e16582441d28f0c9de8f"
os.environ["UAIZAPI_FINANCE_ALLOWED_CHAT_IDS"]="120363040785758584@g.us"
os.environ["UAIZAPI_FINANCE_ALLOWED_CHAT_NAMES"]="FINANCEIRO OFi7,financeiro"

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()