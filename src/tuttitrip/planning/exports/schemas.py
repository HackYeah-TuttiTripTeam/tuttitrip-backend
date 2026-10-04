"""DTOs of the Google exports."""

from uuid import UUID

from pydantic import BaseModel, Field


class CalendarExportRead(BaseModel):
    """Result of saving a plan in the user's Google Calendar."""

    plan_id: UUID
    calendar_id: str = Field(description="Id of the secondary calendar.")
    calendar_url: str = Field(description="Google Calendar, where it is listed.")
    events: int = Field(ge=0, description="Events in the calendar after the save.")
    removed: int = Field(
        ge=0, description="Events of an older version that this save removed."
    )
    created: bool = Field(description="False when an earlier calendar was updated.")


class DriveExportRead(BaseModel):
    """Result of exporting a plan to the user's Google Drive."""

    plan_id: UUID
    file_id: str
    web_view_link: str = Field(description="Link that opens the document.")
    created: bool = Field(description="False when an earlier document was updated.")
