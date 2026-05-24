web: gunicorn lce_platform.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 2 --timeout 120 --chdir lce_platform
worker: python lce_platform/manage.py qcluster
release: python lce_platform/manage.py migrate --noinput
