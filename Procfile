release: python manage.py migrate
web: gunicorn django_chatbot.wsgi:application --log-file - --workers 1
