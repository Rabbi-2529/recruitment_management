"""
Passenger entry point for cPanel "Setup Python App".

cPanel settings:
  Application root:            the folder that contains this file (e.g. careers)
  Application URL:             careers.iglweb.com  (or careers.iglweb.com/careers)
  Application startup file:    passenger_wsgi.py
  Application Entry point:     application

If the Application URL has a path (careers.iglweb.com/careers), set the
URL_PREFIX below to "/careers". If it is just careers.iglweb.com, leave it "".
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["DJANGO_DEBUG"] = "0"  # live server: DEBUG is always False

URL_PREFIX = ""  # e.g. "/careers" for careers.iglweb.com/careers
os.environ.setdefault("DJANGO_URL_PREFIX", URL_PREFIX)

import django  # noqa: E402
from django.urls import set_script_prefix  # noqa: E402

django.setup(set_prefix=False)
set_script_prefix((os.environ["DJANGO_URL_PREFIX"].rstrip("/") or "") + "/")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
