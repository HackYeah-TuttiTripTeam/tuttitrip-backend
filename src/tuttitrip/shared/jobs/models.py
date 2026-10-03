"""Tables shared with tuttitrip-worker (the worker role may write them).

Owned and migrated by the backend; the worker never runs DDL.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class WorkerHeartbeat(Base):
    """Written every ~30 s by the worker's scheduled heartbeat workflow."""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    env: Mapped[str] = mapped_column(String(63), index=True)
    contract_version: Mapped[int]
    min_contract_version: Mapped[int]
    app_version: Mapped[str] = mapped_column(String(200))
    last_seen: Mapped[datetime] = mapped_column(server_default=func.now())


class JobResult(Base):
    """Large or persistent workflow results, keyed by DBOS workflow id."""

    __tablename__ = "job_results"

    workflow_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    workflow_name: Mapped[str] = mapped_column(String(100), index=True)
    contract_version: Mapped[int]
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
