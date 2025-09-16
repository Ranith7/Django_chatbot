"""
WSGI config for django_chatbot project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os
from django.core.wsgi import get_wsgi_application

# Set Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_chatbot.settings')

# Setup Django
import django
django.setup()

# Automatically run migrations on startup
from django.core.management import call_command
try:
    call_command("migrate", interactive=False)
except Exception as e:
    print("Migration failed:", e)

# WSGI application
application = get_wsgi_application()
