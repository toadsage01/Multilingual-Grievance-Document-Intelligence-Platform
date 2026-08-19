"""Cache helpers — strict-mode caching facade.

If a test wants to swap to fakeredis, it just overrides the Django
CACHES["default"] setting and everything else keeps working because
we go through django.core.cache.
"""
from django.core.cache import cache as _default_cache

# Type alias so callers don't need to import django.core.cache
Cache = type(_default_cache)


def get_cache() -> Cache:
    return _default_cache
