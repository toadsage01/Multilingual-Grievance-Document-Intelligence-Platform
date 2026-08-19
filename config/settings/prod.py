"""Production overrides. Fail closed: no debug, strict hosts, secure proxy."""
import os
from .base import *  # noqa: F401, F403

DEBUG = False
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# In prod, RQ jobs actually enqueue
RQ_QUEUES["default"]["ASYNC"] = True  # type: ignore[name-defined]

# More restrictive throttle in prod — protect LLM quota
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {  # type: ignore[index]
    "anon": "20/min",
    "user": "60/min",
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "default"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "apps": {"level": "INFO", "handlers": ["console"], "propagate": False},
    },
}
