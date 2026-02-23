from io import BytesIO
from traceback import format_exc
from typing import Any
from uuid import UUID
import aioboto3
from loguru import logger
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError
from evedesign_server.api.api_schemas import JobSubmissionPayload, JobSubmissionResponse, JobResultResponse, \
    JobListResponse, JobSummary, BalanceResponse
from evedesign_server.api.auth import CurrentUserDep
from evedesign_server.api.db_session import DBSessionDep, lifespan
from evedesign_server.db.controller import (
    create_job, add_to_queue, get_job_by_id, get_server_user_by_id, list_jobs_for_user, spec_path_for_job_id,
    result_path_for_job_id, balance_for_user
)
from evedesign_server.config import Settings

s = Settings()

web_app = FastAPI(lifespan=lifespan)
web_app.add_middleware(
    CORSMiddleware,  # noqa
    allow_origins=[s.frontend_url],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization"],
)

@web_app.post("/job")
async def submit_job(
    job_definition: JobSubmissionPayload,
    current_user: CurrentUserDep,
    session: DBSessionDep,
    client_ip: str = Header(None, alias="X-Forwarded-For")
) -> JobSubmissionResponse:
    logger.info(client_ip)

    # check available user credits here or halt job submission
    # with "insufficient credits" error;
    # note this may be None if no server user entry added to correspond
    # to auth table
    user = await get_server_user_by_id(
        user_id=current_user.sub, session=session
    )

    if user is None:
        raise HTTPException(
            status_code=401, detail="User not activated for job submission"
        )

    # deny submission if not enough credit
    balance = await balance_for_user(
        current_user.sub, session,
    )

    # commit any changes to balance right away, start new transaction for submission
    if session.dirty:
        await session.commit()

    if balance is None or balance <= 0:
        raise HTTPException(
            status_code=401, detail="Insufficient compute credit"
        )

    try:
        # initialize compute job
        job = await create_job(
            name=job_definition.name,
            spec=job_definition.spec,
            project_id=job_definition.project_id,
            public=job_definition.public,
            parent_job_id=job_definition.parent_job_id,
            ip_address=client_ip,
            user_id=current_user.sub,
            session=session,
        )

        # add to job queue
        await add_to_queue(
            job=job,
            priority= user.job_priority,
            session=session
        )

        # upload input spec to s3
        spec_dumped = job_definition.spec.model_dump_json().encode()
        aws_session = aioboto3.Session()
        async with aws_session.client(
            "s3",
            region_name=s.aws_region_name,
            aws_access_key_id=s.aws_access_key_id,
            aws_secret_access_key=s.aws_secret_access_key,
        ) as client:
            await client.upload_fileobj(
                BytesIO(spec_dumped),
                s.aws_s3_bucket_name,
                spec_path_for_job_id(job.id)
            )

        await session.commit()
        return JobSubmissionResponse(
            job_id=job.id
        )
    except IntegrityError:
        logger.error(format_exc())
        # will be raised e.g. for invalid parent project id
        raise HTTPException(
            status_code=422, detail="Inconsistent references in job definition"
        )
    except Exception:
        logger.error(format_exc())
        raise HTTPException(
            status_code=500, detail="Error processing submission request"
        )

@web_app.get("/job")
async def list_jobs(
    current_user: CurrentUserDep,
    session: DBSessionDep,
    client_ip: str = Header(None, alias="X-Forwarded-For")
) -> JobListResponse:
    logger.info(client_ip)

    # return empty job list for public account etc. if specified
    if s.hidden_result_list_user_ids is not None and str(current_user.sub) in s.hidden_result_list_user_ids:
        jobs = []
    else:
        jobs = await list_jobs_for_user(
            current_user_id=current_user.sub, session=session
        )

    return JobListResponse(
        jobs=[
            JobSummary(
                **job.__dict__
            ) for job in jobs
        ]
    )

@web_app.get("/job/{job_id}")
async def get_job_results(
    job_id: UUID,
    current_user: CurrentUserDep,
    session: DBSessionDep,
    client_ip: str = Header(None, alias="X-Forwarded-For")
) -> JobResultResponse | Any:
    logger.info(client_ip)

    job = await get_job_by_id(
        job_id, current_user_id=current_user.sub, session=session
    )
    if job is None:
        raise HTTPException(
            status_code=400, detail="Invalid job or missing permissions"
        )

    summary_response = JobResultResponse(
        **job.__dict__
    )

    if job.status != "finished":
        return summary_response

    try:
        # https://stackoverflow.com/questions/69617252/response-file-stream-from-s3-fastapi
        aws_session = aioboto3.Session()
        async def s3_stream():
            async with aws_session.client(
               "s3",
                region_name=s.aws_region_name,
                aws_access_key_id=s.aws_access_key_id,
                aws_secret_access_key=s.aws_secret_access_key,
            ) as client:
                result = await client.get_object(
                    Bucket=s.aws_s3_bucket_name, Key=result_path_for_job_id(job_id)
                )
                # inject other fields into stream response
                yield (summary_response.model_dump_json()[:-1] + ', "results": ').encode()
                async for chunk in result["Body"]:
                    yield chunk
                yield '}'.encode()

        return StreamingResponse(s3_stream())
    except Exception:
        raise HTTPException(
            status_code=500, detail="Error returning job results"
        )

# # alternative with regular boto3:
# # define outside function
# s3 = boto3.client(
#     "s3", aws_access_key_id="...", aws_secret_access_key="..."
# )
# result = s3.get_object(
#     Bucket="designserver-storage", Key="demofile1"
# )
# return StreamingResponse(
#     content=result["Body"].iter_chunks()
# )
# """

@web_app.get("/balance")
async def get_balance(
    current_user: CurrentUserDep,
    session: DBSessionDep,
    client_ip: str = Header(None, alias="X-Forwarded-For")
) -> BalanceResponse:
    logger.info(client_ip)

    balance = await balance_for_user(
        current_user.sub, session
    )
    # commit updated balance
    await session.commit()

    # return balance as available compute time in minutes rather than raw budget
    balance = balance / (s.compute_cost_per_second["pipeline"] * 60)

    return BalanceResponse(
        balance=balance
    )

@web_app.get("/")
async def index(
    current_user: CurrentUserDep,
    session: DBSessionDep,  # noqa
    client_ip: str = Header(None, alias="X-Forwarded-For")
):
    logger.info(client_ip)

    return {
        "user": current_user.sub,
    }
