release: python manage.py migrate --noinput
web: gunicorn django_chatbot.wsgi:application --log-file - --workers 1
