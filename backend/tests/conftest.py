"""Local HTTP integration tests intentionally use non-Secure cookies."""

import os

os.environ.setdefault("APP_ENV", "local")
