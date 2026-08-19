"""Base settings shared by every environment.

Environment-specific overrides live in dev.py / prod.py and pull real
secrets from environment variables. Nothing here should hard-code a key.
"""
from pathlib import Path
import os
import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# env handling: read .env if present (local dev), fall back to os.environ
env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-do-not-use-in-prod")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# -- Applications ---------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 3rd-party
    "rest_framework",
    "drf_spectacular",
    "django_rq",
    # local apps — note the order: tenancy first so the RLS middleware
    # can run before any DB query from other apps
    "apps.tenancy",
    "apps.ingestion",
    "apps.retrieval",
    "apps.chat",
    "apps.grievances",
    "apps.translation",
    "apps.llm",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # sets app.current_tenant on the connection before any query runs
    "apps.tenancy.middleware.TenantContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# -- Database -------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgresql://setu:setu@localhost:5432/setu",
    )
}
# Required by pgvector + raw SQL migrations
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Auth / password hashing is fine at defaults
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# -- DRF ------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "30/min",
        "user": "120/min",
    },
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Setu API",
    "DESCRIPTION": "Multilingual grievance and document intelligence platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# -- Redis / caching -----------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

RQ_QUEUES = {
    "default": {
        "HOST": env("REDIS_HOST", default="localhost"),
        "PORT": env("REDIS_PORT", default=6379),
        "DB": env("REDIS_DB", default=0),
        "ASYNC": False,  # flipped to True in prod.py
    },
    "ingest": {
        "HOST": env("REDIS_HOST", default="localhost"),
        "PORT": env("REDIS_PORT", default=6379),
        "DB": env("REDIS_DB", default=0),
    },
}

# -- Embeddings / LLM ----------------------------------------------------
EMBEDDING_MODEL_NAME = env("EMBEDDING_MODEL_NAME", default="intfloat/multilingual-e5-base")
EMBEDDING_DIM = 768  # matches multilingual-e5-base
DEFAULT_LANGUAGE = env("DEFAULT_LANGUAGE", default="en")
SUPPORTED_LANGUAGES = [
    "en", "hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa", "or", "as", "ur"
]

# RAG tuning knobs — exposed as env so we can iterate without code change
RAG_TOP_K = env.int("RAG_TOP_K", default=5)
RAG_CONFIDENCE_THRESHOLD = env.float("RAG_CONFIDENCE_THRESHOLD", default=0.72)

# LLM provider rotation
LLM_PRIMARY_PROVIDER = env("LLM_PRIMARY_PROVIDER", default="groq")
LLM_FALLBACK_PROVIDER = env("LLM_FALLBACK_PROVIDER", default="gemini")
LLM_CIRCUIT_FAILURE_THRESHOLD = env.int("LLM_CIRCUIT_FAILURE_THRESHOLD", default=5)
LLM_CIRCUIT_COOLDOWN_SECONDS = env.int("LLM_CIRCUIT_COOLDOWN_SECONDS", default=60)

GROQ_API_KEY = env("GROQ_API_KEY", default="")
GROQ_MODEL = env("GROQ_MODEL", default="llama-3.1-8b-instant")
GROQ_BASE_URL = env("GROQ_BASE_URL", default="https://api.groq.com/openai/v1")

GEMINI_API_KEY = env("GEMINI_API_KEY", default="")
GEMINI_MODEL = env("GEMINI_MODEL", default="gemini-1.5-flash")

# Bhashini is optional — wired through the Translator interface
BHASHINI_API_KEY = env("BHASHINI_API_KEY", default="")
BHASHINI_USER_ID = env("BHASHINI_USER_ID", default="")
