"""Django settings for Spotter Fuel Route Optimizer project."""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file if present
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-spotter-fuel-route-eval-key-production-change-me"
)

DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("ALLOWED_HOSTS", "*").split(",")
    if host.strip()
]

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party apps
    "rest_framework",
    "corsheaders",
    # Local apps
    "routing",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
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
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database configuration
# Default to SQLite for zero-friction local execution, but PostgreSQL ready
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    import dj_database_url
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# Cache configuration (Used for geocoding and route caching)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "spotter-fuel-cache",
        "TIMEOUT": 86400,  # 24 hours default TTL
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    "EXCEPTION_HANDLER": "routing.views.custom_exception_handler",
}

# CORS settings
CORS_ALLOW_ALL_ORIGINS = True

# Routing & Vehicle Settings
VEHICLE_MAX_RANGE_MILES = float(os.getenv("VEHICLE_MAX_RANGE_MILES", "500.0"))
VEHICLE_MPG = float(os.getenv("VEHICLE_MPG", "10.0"))
VEHICLE_TANK_CAPACITY_GALLONS = VEHICLE_MAX_RANGE_MILES / VEHICLE_MPG  # 50.0 gallons
CORRIDOR_SEARCH_RADIUS_MILES = float(os.getenv("CORRIDOR_SEARCH_RADIUS_MILES", "10.0"))

# External Routing & Geocoding APIs
ROUTING_PROVIDER = os.getenv("ROUTING_PROVIDER", "osrm").lower()
ROUTING_API_KEY = os.getenv("ROUTING_API_KEY", "")
OSRM_SERVER_URL = os.getenv("OSRM_SERVER_URL", "https://router.project-osrm.org")
GEOCODING_PROVIDER = os.getenv("GEOCODING_PROVIDER", "photon").lower()
GEOCODING_USER_AGENT = os.getenv("GEOCODING_USER_AGENT", "SpotterFuelRouteOptimizer/1.0")
