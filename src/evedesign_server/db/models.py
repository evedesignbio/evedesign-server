"""
Database models for designserver API

Note: using Postgres special features, assuming we only use this type of SQL database
"""
import datetime
from typing import Any, Literal, Optional
from uuid import UUID, uuid4
from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.engine import Dialect
from sqlalchemy.dialects.postgresql import JSONB, INET
from sqlalchemy.types import TypeDecorator
from evedesign_server.schemas import InputSpecTypeKeys

AUTH_USERS_TABLENAME = "auth.users.id"
PROJECTS_TABLENAME = "designserver_projects"
JOBS_TABLENAME = "designserver_jobs"
QUEUE_TABLENAME = "designserver_queue"
SERVER_USERS_TABLENAME = "designserver_users"

# following from https://github.com/litestar-org/advanced-alchemy/blob/main/advanced_alchemy/types/datetime.py
# under MIT license
class DateTimeUTC(TypeDecorator[datetime.datetime]):
    """Timezone Aware DateTime.

    Ensure UTC is stored in the database and that TZ aware dates are returned for all dialects.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    @property
    def python_type(self) -> type[datetime.datetime]:
        return datetime.datetime

    def process_bind_param(self, value: Optional[datetime.datetime], dialect: Dialect) -> Optional[datetime.datetime]:
        if value is None:
            return value
        if not value.tzinfo:
            msg = "tzinfo is required"
            raise TypeError(msg)
        return value.astimezone(datetime.timezone.utc)

    def process_result_value(self, value: Optional[datetime.datetime], dialect: Dialect) -> Optional[datetime.datetime]:
        if value is None:
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value


class Base(DeclarativeBase):
    # https://docs.sqlalchemy.org/en/14/orm/extensions/asyncio.html#preventing-implicit-io-when-using-asyncsession
    __mapper_args__ = {
        "eager_defaults": True
    }

    type_annotation_map = {
        dict[str, Any]: JSONB,
        datetime.datetime: DateTimeUTC,
        # UUID: GUID,
        # core_uuid.UUID: GUID,
        # datetime.date: Date,
    }

    # primary key (for now uuidv4 but may switch to uuidv7 for better index performance if needed)
    id: Mapped[UUID] = mapped_column(default=uuid4, primary_key=True)


class User(Base):
    __tablename__ = "users"
    __table_args__ = {
        "schema": "auth", "keep_existing": True
    }

    # user email address
    email: Mapped[str]


class ServerUser(Base):
    """
    Per-user allocated compute credit
    """
    __tablename__ = SERVER_USERS_TABLENAME

    # user/owner who created this run (foreign key to user schema);
    # following https://supabase.com/docs/guides/auth/managing-user-data
    id: Mapped[UUID] = mapped_column(
        ForeignKey(AUTH_USERS_TABLENAME), primary_key=True
    )
    user: Mapped[User] = relationship()

    job_priority: Mapped[float] = mapped_column(default=1)
    balance: Mapped[float] = mapped_column(default=0)


class Project(Base):
    """
    Minimal skeleton for project structure.

    Note: will need to be integrated with proper permission model,
     and user/team/organization handling
    """
    __tablename__ = PROJECTS_TABLENAME

    # non-unique name (required)
    name: Mapped[str]

JobStatus = Literal[
    "initialized",  # job created and in queue but not yet submitted to worker
    "pending",  # job fetched from queue by worker
    "running",  # job is ongoing
    "finished",  # job is finished
    "failed",  # job failed due to error
    "terminated",  # job was terminated by worker
    "paused",  # job was halted by worker
]

class ComputeJob(Base):
    """
    Single compute job. Holds general information about job, its status,
    and results (via JSON field)
    """
    __tablename__ = JOBS_TABLENAME

    # optional human-readable job name (not unique)
    name: Mapped[str | None]

    # user/owner who created this run (foreign key to user schema)
    created_by_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SERVER_USERS_TABLENAME}.id")
    )
    created_by: Mapped[ServerUser] = relationship()

    # project this job belongs to (there may be runs that do not belong to a project, coded by None)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{PROJECTS_TABLENAME}.id")
    )
    project: Mapped[Project] = relationship()

    # reference to parent computate job (e.g. codon optimization or rescoring)
    parent_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{JOBS_TABLENAME}.id")
    )
    parent_job: Mapped["ComputeJob"] = relationship()

    # job definition (must be defined for a valid job)
    spec: Mapped[dict[str, Any]]

    # duplicated version of spec->type for decoupling queries on jon type
    # from underlying pipeline execution (even if it means duplication)
    type: Mapped[InputSpecTypeKeys]

    # if True, job can be viewed by anyone
    public: Mapped[bool] = mapped_column(
        default=False
    )

    # job execution status
    status: Mapped[JobStatus] = mapped_column(default="initialized")

    # message (for exceptions, termination codes, etc.; keep these out of results field)
    message: Mapped[str | None] = mapped_column(default=None)

    # current execution step (if pipeline)
    step: Mapped[int | None] = mapped_column(default=None)

    # time of initial job creation, automatically created
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTimeUTC(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    # time of last update to record (may also happen after job is finished, e.g. re billing/cost updates);
    # automatically changed upon updated
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTimeUTC(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    # time actual computation starts (beginning of cost tracking)
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTimeUTC(timezone=True), default=None
    )

    # time actual computation ends or fails (end of cost tracking)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTimeUTC(timezone=True), default=None
    )

    # has job cost been billed to user/organization account?
    billed: Mapped[bool] = mapped_column(
        default=False
    )

    # cost accrued when executing the job.
    # note: 0 = zero cost, None = cost not yet stored
    cost: Mapped[float | None] = mapped_column(
        default=None
    )

    # name of worker job is/was executing on
    # (if ever needed, could normalize this to a Worker table but keep as simple as possible
    # for now)
    worker_name: Mapped[str | None] = mapped_column(
        default=None
    )

    # identifier of job on worker, e.g. modal or cluster job identifier (only unique to worker)
    worker_job_id: Mapped[str | None] = mapped_column(
        default=None
    )

    # any other metadata
    other_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        default=None
    )

    # submission IP address
    ip_address: Mapped[str | None] = mapped_column(type_=INET, default=None)


class JobQueue(Base):
    """
    Simple job queue (assuming sufficient for beginning instead of
    redis queue or the like)

    To be used with Postgres FOR UPDATE SKIP LOCKED for efficient
    job allocation to different workers

    Note: we do not reference the ComputeJob class by primary key
     but by UUID so this basic queue can easily be substituted
     if ever needed
    """
    __tablename__ = QUEUE_TABLENAME

    # link to actual job definition that should be executed
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{JOBS_TABLENAME}.id")
    )

    job = relationship(ComputeJob)

    # time of initial job queue entry creation, automatically created
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTimeUTC(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    priority: Mapped[float] = mapped_column(default=1)

    # True if job has been received by worker and job is executing
    # (will be deleted from queue once job has finished)
    received: Mapped[bool] = mapped_column(default=False)

    # tracks if job was resubmitted (e.g. give higher priority due to prior termination)
    resubmitted: Mapped[bool] = mapped_column(default=False)
