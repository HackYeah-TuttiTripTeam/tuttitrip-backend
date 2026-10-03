"""Interview endpoints (the AG-UI endpoint for CopilotKit goes here)."""

from fastapi import APIRouter

router = APIRouter(prefix="/interview", tags=["interview"])
