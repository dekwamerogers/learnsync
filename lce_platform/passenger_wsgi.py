"""
Passenger WSGI entry point for cPanel / Phusion Passenger hosting.

This file must sit alongside manage.py (i.e. inside lce_platform/).
cPanel's "Setup Python App" tool will point the Application Root here.

If mysqlclient is unavailable and you are using PyMySQL instead, uncomment
the two lines below before the Django import.
"""
import sys
import os

# ── Optional: PyMySQL compatibility shim (only if mysqlclient is not installed)
# import pymysql
# pymysql.install_as_MySQLdb()

# Ensure Django's project package is on the Python path.
# Passenger sets the working directory to the Application Root, so this is
# usually not needed — but it makes things explicit.
REPO_DIR = os.path.dirname(os.path.abspath(__file__))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lce_platform.settings')

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
