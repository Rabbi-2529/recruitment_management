import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent



def _secret_key():
    """Use DJANGO_SECRET_KEY if set, otherwise a random key stored in secret_key.txt (created once)."""
    if os.environ.get("DJANGO_SECRET_KEY"):
        return os.environ["DJANGO_SECRET_KEY"]
    key_file = BASE_DIR / "secret_key.txt"
    if not key_file.exists():
        from django.core.management.utils import get_random_secret_key

        key_file.write_text(get_random_secret_key())
    return key_file.read_text().strip()


def _env_list(name, default):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = _secret_key()
DEBUG = False  # set DJANGO_DEBUG=1 only on your own computer for debugging
if os.environ.get("DJANGO_DEBUG") == "1":
    DEBUG = True
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,careers.iglweb.com,www.careers.iglweb.com")
CSRF_TRUSTED_ORIGINS = _env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS", "https://careers.iglweb.com,https://www.careers.iglweb.com"
)

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# NOTE: django.contrib.admin is intentionally NOT installed.
# The admin panel is a custom one living at /panel/.
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "recruitment",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves /static/ on the live server
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "recruitment.context_processors.hr_contact",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DJANGO_DB_PATH", BASE_DIR / "db.sqlite3"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

# Set DJANGO_URL_PREFIX=/careers when the site lives at careers.iglweb.com/careers
URL_PREFIX = os.environ.get("DJANGO_URL_PREFIX", "").strip().rstrip("/")
if URL_PREFIX and not URL_PREFIX.startswith("/"):
    URL_PREFIX = "/" + URL_PREFIX
if URL_PREFIX:
    FORCE_SCRIPT_NAME = URL_PREFIX
    SESSION_COOKIE_PATH = URL_PREFIX + "/"
    CSRF_COOKIE_PATH = URL_PREFIX + "/"
STATIC_URL = URL_PREFIX + "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = Path(os.environ.get("DJANGO_STATIC_ROOT", BASE_DIR / "staticfiles"))
# WhiteNoise also serves straight from STATICFILES_DIRS, so CSS/JS work even if collectstatic was not run
WHITENOISE_USE_FINDERS = True
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Uploaded CVs. They are private: the panel serves them through a login-checked view,
# so MEDIA_ROOT does not have to be reachable from the web server.
MEDIA_URL = URL_PREFIX + "/media/"
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT", BASE_DIR / "media"))
CV_MAX_BYTES = 1 * 1024 * 1024  # 1 MB

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "panel:login"
LOGIN_REDIRECT_URL = "panel:dashboard"

FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10000

# Contact info shown to selected candidates
HR_PHONE = "01958666999"
HR_EMAIL = "hr@iglweb.com"
ROOT_EMAIL = "root@iglweb.com"
