"""
Pluggable job dispatch for the submit_job() endpoint.

Select a dispatcher by setting the environment variable
DESIGNSERVER_JOB_DISPATCHER="package.module:ClassName"; the class is
instantiated without arguments. If unset, jobs go into the evedesign job queue.
"""
from functools import cache
from importlib import import_module
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from evedesign_server.config import Settings
from evedesign_server.db.controller import add_to_queue
from evedesign_server.db.models import ComputeJob, ServerUser


@runtime_checkable
class JobDispatcher(Protocol):
    async def dispatch(
        self, job: ComputeJob, spec_json: bytes, user: ServerUser, session: AsyncSession
    ) -> None:
        """
        Called inside the submission transaction, before commit.
        Raise to abort the submission (raise HTTPException for a custom response).
        """
        ...

    async def after_commit(
        self, job: ComputeJob, spec_json: bytes, user: ServerUser
    ) -> None:
        """
        Called after the job has been committed; use this for calls to external systems
        (e.g. webhooks) so they never see a job that does not exist in the database.
        """
        ...


class QueueDispatcher:
    """Default: add job to the evedesign job queue table."""

    async def dispatch(
        self, job: ComputeJob, spec_json: bytes, user: ServerUser, session: AsyncSession
    ) -> None:
        await add_to_queue(job=job, priority=user.job_priority, session=session)

    async def after_commit(
        self, job: ComputeJob, spec_json: bytes, user: ServerUser
    ) -> None:
        pass


def load_dispatcher(path: str | None) -> JobDispatcher:
    if path is None:
        return QueueDispatcher()

    module_name, sep, attr = path.partition(":")
    if not sep or not attr:
        raise ValueError(
            f"Invalid job dispatcher '{path}', expected format 'package.module:ClassName'"
        )

    dispatcher = getattr(import_module(module_name), attr)()
    if not isinstance(dispatcher, JobDispatcher):
        raise TypeError(f"Job dispatcher '{path}' does not implement JobDispatcher")

    return dispatcher


@cache
def get_dispatcher() -> JobDispatcher:
    return load_dispatcher(Settings().job_dispatcher)
