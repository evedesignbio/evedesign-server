import datetime
from typing import Sequence
from uuid import UUID, uuid4
from sqlalchemy import select, delete, update, bindparam
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from evedesign_server.db.models import ComputeJob, JobQueue, ServerUser, JobStatus
from evedesign_server.schemas import InputSpec, condense_spec
from evedesign_server.config import Settings

async def get_server_user_by_id(
    user_id: UUID,
    session: AsyncSession,
) -> ServerUser | None:
    user = (
        await session.scalar(
            select(ServerUser).where(
                ServerUser.id == user_id
            )
        )
    )

    return user

async def create_job(
    name: str | None,
    spec: InputSpec,
    project_id: str | None,
    public: bool,
    parent_job_id: UUID | None,
    ip_address: str | None,
    user_id: UUID,
    session: AsyncSession
) -> ComputeJob:
    # TODO: add project; will need to verify user has permission to write to project
    assert project_id is None, "project linking not yet implemented"

    # note: for now we set parent jobs without checking access privilege;
    # user will need to have access to derive a job from existing jobs anyways
    job = ComputeJob(
        id=uuid4(),  # manually create job ID so it is available right away
        name=name,
        created_by_id=user_id,
        project_id=None,
        public=public,
        spec=condense_spec(spec).model_dump(),
        type=spec.key,
        parent_job_id=parent_job_id,
        ip_address=ip_address
    )

    session.add(job)
    return job

async def add_to_queue(
    job: ComputeJob,
    priority: float,
    session: AsyncSession,
) -> JobQueue:
    queue_entry = JobQueue(
        job=job,
        priority=priority
    )

    session.add(queue_entry)
    return queue_entry

async def get_job_by_id(
    job_id: UUID,
    current_user_id: UUID,
    session: AsyncSession,
) -> ComputeJob | None:
    job = (
        await session.scalar(
            select(ComputeJob).where(
                (ComputeJob.id == job_id) & (ComputeJob.public | (ComputeJob.created_by_id == current_user_id))
            )
        )
    )

    return job

async def list_jobs_for_user(
    current_user_id: UUID,
    session: AsyncSession,
) -> Sequence[ComputeJob]:
    jobs = (
        await session.scalars(
            # select(ComputeJob.name, ComputeJob.status, ComputeJob.type, ComputeJob.created_at).where(
            #    ComputeJob.created_by_id == current_user_id
            #
            select(
                ComputeJob
            ).where(
                ComputeJob.created_by_id == current_user_id
            ).order_by(
                ComputeJob.created_at.desc()
            )
        )
    ).all()

    return jobs

def compute_job_cost_and_update(job: ComputeJob, s: Settings | None = None) -> float:
    s = s or Settings()
    job_timediff = (job.finished_at - job.started_at).total_seconds()
    # also add fixed overhead for job
    job_timediff += s.job_time_overhead_seconds[job.type]
    job_cost = job_timediff * s.compute_cost_per_second[job.type]

    # update job
    job.cost = job_cost
    job.billed = True

    return job_cost

async def balance_for_user(
    current_user_id: UUID,
    session: AsyncSession,
) -> float | None:
    s = Settings()

    user = await session.scalar(
        select(ServerUser).where(
            ServerUser.id == current_user_id
        )
    )

    # initialize user if not yet present
    if user is None:
        user = ServerUser(
            id=current_user_id,
            balance=s.new_user_default_balance,
            job_priority=s.new_user_default_priority
        )
        session.add(user)
        return user.balance

    current_balance = user.balance

    # retrieve any unbilled jobs, lock them for update to avoid race conditions
    finished_jobs = await session.scalars(
        select(
            # ComputeJob.started_at, ComputeJob.finished_at
            ComputeJob
        ).where(
            (ComputeJob.created_by_id == current_user_id) &
            ComputeJob.started_at.isnot(None) &
            ComputeJob.finished_at.isnot(None) &
            (ComputeJob.billed == False)
        ).with_for_update()
    )

    # accumulate costs and mark job as billed
    spent = 0
    for job in finished_jobs:
        # compute cost for current job and update fields
        job_cost = compute_job_cost_and_update(job, s)

        # accumulate cost across different jobs
        spent += job_cost

    # update balance (will need to be commited as part of session outside)
    if spent > 0:
        # user.balance = user.balance - spent
        # perform "delta" update to avoid any potential race conditions
        await session.execute(
            update(ServerUser).where(ServerUser.id == current_user_id).values({
                ServerUser.balance: ServerUser.balance - spent
            })
        )
        await session.commit()

    # return current balance
    return current_balance - spent


def update_balance_for_all_sync(session: Session):
    s = Settings()

    finished_jobs = session.scalars(
        select(
            ComputeJob
        ).where(
            ComputeJob.started_at.isnot(None) &
            ComputeJob.finished_at.isnot(None) &
            (ComputeJob.billed == False)
        ).with_for_update()
    )

    # iterate jobs across different users, doing a manual groupby
    # here for ease and flexibility in handling cost computations
    user_id_to_update = {}

    for job in finished_jobs:
        # compute cost for current job and update fields
        job_cost = compute_job_cost_and_update(job, s)

        # subtract from respective user balance
        # job.created_by.balance = job.created_by.balance - job_cost
        user_id_to_update[job.created_by_id] = user_id_to_update.get(
            job.created_by_id, 0
        ) + job_cost

    # update user by user, and do a "delta update" for locked rows above
    # rather than setting absolute value as ORM-style update would do.
    # Not efficient but the bulk update strategies tried below do not
    # work out the box as expected
    for user_id, spent in user_id_to_update.items():
        session.execute(
            update(ServerUser).where(ServerUser.id == user_id).values({
                ServerUser.balance: ServerUser.balance - spent
            })
        )

    # fails with "InvalidRequestError: bulk synchronize of persistent objects not supported when
    #   using bulk update with additional WHERE criteria right now.
    #   add synchronize_session=None execution option to bypass synchronize of persistent objects."
    # if len(user_id_to_update) > 0:
    #     stmt = update(ServerUser).where(
    #         ServerUser.id == bindparam("_id")
    #     ).values({
    #         ServerUser.balance: ServerUser.balance + bindparam("_update")
    #     })
    #
    #     records = [
    #         {"_id": _id, "_update": _update} for _id, _update in user_id_to_update.items()
    #     ]
    #     session.execute(stmt, records)

    # bulk update unfortunately doesn't seem to work with "delta update"
    # if len(user_id_to_update) > 0:
    #     session.execute(
    #         update(ServerUser),
    #         [
    #             {
    #                 "id": user_id,
    #                 ServerUser.balance: ServerUser.balance + balance_update,
    #                 # "balance": balance_update,
    #             } for user_id, balance_update in user_id_to_update.items()
    #         ]
    #     )


def get_job_queue_entries_sync(max_num_jobs: int, session: Session):
    # use sync session for backend
    enqueued_jobs = session.scalars(
        select(
            JobQueue
        ).where(
            JobQueue.received == False
        ).order_by(
            JobQueue.priority.desc(), JobQueue.created_at.asc()
        ).limit(
            max_num_jobs
        ).with_for_update(
            skip_locked=True
        )
    )

    return enqueued_jobs

def get_job_by_id_sync(
    job_id: UUID,
    session: Session,
) -> ComputeJob | None:
    # for backend use only, no auth check
    job = session.scalar(
        select(ComputeJob).where(
            ComputeJob.id == job_id
        )
    )

    return job

def update_job_in_place(
    job: ComputeJob,
    session: Session,
    status: JobStatus | None = None,
    spec: InputSpec | None = None,
    message: str | None = None,
    worker_name: str | None = None,
    worker_job_id: str | None = None,
) -> None:
    if status is not None:
        job.status = status

        # if we switch job to running, set start time (start of billing window)
        if status == "running":
            job.started_at = datetime.datetime.now(datetime.timezone.utc)

        # if we end job, set finished time (end of billing window)
        if status in {"finished", "failed", "terminated"}:
            job.finished_at = datetime.datetime.now(datetime.timezone.utc)

        # if job properly finished, we can remove it from the job queue
        if status == "finished":
            delete_queue_entries_for_job_sync(
                job_id=job.id, session=session
            )

    if message is not None:
        job.message = message

    if worker_name is not None:
        job.worker_name = worker_name

    if worker_job_id is not None:
        job.worker_job_id = worker_job_id

    if spec is not None:
        job.spec = condense_spec(spec).model_dump()

def delete_queue_entries_for_job_sync(
    job_id: UUID,
    session: Session
):
    session.execute(
        delete(JobQueue).where(JobQueue.job_id == job_id)
    )

def spec_path_for_job_id(job_id: UUID):
    return f"specs/{job_id}"

def result_path_for_job_id(job_id: UUID):
    return f"results/{job_id}"
