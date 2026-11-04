from pydantic import Field

from .schemas import StrictModel


class CleanupRequest(StrictModel):
    scanId: str = Field(min_length=1, max_length=80)
    itemIds: list[str] = Field(min_length=1, max_length=10000)


class ProjectRemovalRequest(StrictModel):
    expectedVersion: int = Field(ge=1)
    confirmName: str = Field(min_length=1, max_length=120)


class TrashedProjectRequest(ProjectRemovalRequest):
    projectId: str = Field(pattern=r'^p_[0-9a-f]{16}$')


class EmptyTrashRequest(StrictModel):
    projects: list[TrashedProjectRequest] = Field(min_length=1, max_length=10000)
