"""Local dev overrides — debug on, CORS open for frontend prototyping."""
from .base import *  # noqa: F401, F403
from .base import REST_FRAMEWORK

DEBUG = True
ALLOWED_HOSTS = ["*"]

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8080",
]

# in dev we run RQ jobs synchronously so the user can iterate without
# standing up a worker process
RQ_QUEUES["default"]["ASYNC"] = False  # type: ignore[name-defined]
