"""Shared pytest configuration."""

from pydantic_ai import models

# Safety net: fail loudly if any test tries to call a real LLM provider.
models.ALLOW_MODEL_REQUESTS = False
