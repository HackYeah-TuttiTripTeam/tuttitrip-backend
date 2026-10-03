"""Interview endpoints: the AG-UI endpoint (spike)."""

from fastapi import APIRouter
from pydantic_ai.ui.ag_ui import AGUIAdapter
from starlette.requests import Request
from starlette.responses import Response

from tuttitrip.interview.services.interview_agent import interview_agent, new_deps
from tuttitrip.shared.permissions.api import requires
from tuttitrip.shared.permissions.registry import Access, Feature

router = APIRouter(prefix="/interview", tags=["interview"])


@router.post("/agui", dependencies=[requires(Feature.INTERVIEW, Access.WRITE)])
async def agui(request: Request) -> Response:
    """Run the interview agent and stream AG-UI events (SSE).

    Args:
        request: The raw request; the adapter parses ``RunAgentInput``.

    Returns:
        A streaming response with AG-UI events.
    """
    return await AGUIAdapter.dispatch_request(
        request, agent=interview_agent, deps=new_deps()
    )
