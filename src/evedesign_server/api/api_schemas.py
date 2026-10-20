from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from evedesign_server.db.models import JobStatus
from evedesign_server.schemas import InputSpec, InputSpecTypeKeys


class JobSubmissionPayload(BaseModel):
    name: str | None
    project_id: None  # placeholder, eventually UUID | None
    parent_job_id: UUID | None
    public: bool = False
    spec: InputSpec


class JobSubmissionResponse(BaseModel):
    job_id: UUID

class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str | None
    status: JobStatus
    type: InputSpecTypeKeys
    project_id: UUID | None
    parent_job_id: UUID | None
    public: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cost: float | None

class JobListResponse(BaseModel):
    jobs: list[JobSummary]

class JobResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: InputSpecTypeKeys
    status: JobStatus
    name: str | None
    project_id: UUID | None
    parent_job_id: UUID | None
    public: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    # treat as simple dict to avoid costly revalidation on every request
    result: dict[str, Any] | None = None

class BalanceResponse(BaseModel):
    balance: float | None