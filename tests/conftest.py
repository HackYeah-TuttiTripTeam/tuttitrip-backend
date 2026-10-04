"""Shared pytest configuration."""

import os

from pydantic_ai import models

# Safety net: fail loudly if any test tries to call a real LLM provider.
models.ALLOW_MODEL_REQUESTS = False

# Scripted models answer once per request; the interview's card reminder
# (a second model call) is switched on only by the tests of that reminder.
os.environ.setdefault("TUTTITRIP_INTERVIEW__CARD_NUDGES", "0")
