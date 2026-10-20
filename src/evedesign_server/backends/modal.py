"""
Backend based on modal.com environment

Key commands:
 * Deploying entire app: modal deploy -m evedesign_server.backends.modal::app
 * Stopping entire app: modal app stop designserver-api

Other commands:
 * Initialize model volume: modal run -m evedesign_server.backends.modal::app.setup
 * Run local entrypoint: modal run -m evedesign_server.backends.modal::app
"""
import subprocess
import traceback
from uuid import UUID
from loguru import logger
import modal

from evedesign_server.backends.generic import load_spec_from_s3
from evedesign_server.config import Settings
from evedesign_server.db.controller import update_job_in_place
from evedesign_server.schemas import PipelineSpec, ProteinToDnaSpec, SingleMutationScanSpec

APP_NAME = "evedesign-server"
app = modal.App(APP_NAME)
s = Settings()
JWKS_URL_ENV_VAR = "JWKS_URL"
DEFAULT_WORKER_NAME_MODAL = "modal"
MAX_NUM_JOBS_PER_SUBMITTER_RUN = 2

# volume for storing model parameters
model_volume = modal.Volume.from_name(
    "models", create_if_missing=True
)
MODEL_PATH = s.model_param_root

# dictionary for storing results (temporary solution, entries only last for 30 days)
pipeline_results = modal.Dict.from_name(
    "pipeline-results", create_if_missing=True
)

# image for setting up model parameters etc
api_image = modal.Image.debian_slim(
   python_version="3.12"
).pip_install(
    [
        # core package dependencies
        "pydantic", "pydantic-settings", "loguru",
        # api dependencies
        "fastapi[standard]", "uvicorn[standard]",
        "cryptography", "pyjwt", "supabase",
        "sqlalchemy[asyncio]", "asyncpg", "psycopg[binary,pool]",
        "boto3", "aioboto3",
    ]
)

# image for executing model runs
inference_image = modal.Image.micromamba(
   python_version="3.12"
).apt_install(
    "git"
).micromamba_install(
    ["hhsuite", "mmseqs2"], channels=["conda-forge", "bioconda"]
).pip_install(
    [
        "pydantic",
        "pydantic-settings",
        "loguru",
        "dill",
        "transformers",
        "umap-learn",
        "sqlalchemy[asyncio]", "asyncpg", "psycopg[binary,pool]",
        "boto3",
        "evedesign[evmutation2,esm2,mpnn,umap] @ git+https://github.com/evedesignbio/evedesign.git@develop",
    ],
    force_build=False
)

@app.function(
    image=api_image,
    volumes={
        MODEL_PATH: model_volume,
    },
)
def setup():
    """
    Utility function for setting up model files etc.

    Invoke with: modal run -m evedesign_server.backends.modal::app.setup
    """
    from evedesign_server.utils import download_model_files
    download_model_files()

@app.function(
    image=inference_image,
    volumes={
        MODEL_PATH: model_volume,
    },
    gpu="L40S",
    cpu=1,
    memory=8192,
    timeout=24*60*60,
    secrets=[
        modal.Secret.from_name("db-connection-string"),
        modal.Secret.from_name("aws-credentials"),
    ],
)
def execute_pipeline_gpu(
    spec: PipelineSpec | SingleMutationScanSpec | ProteinToDnaSpec,
    job_id: str | UUID | None = None,
):
    """
    GPU-based execution of design pipeline via generic pipeline executor
    """
    # only import execute_pipeline here to keep dependency on evedesign
    # package limited to execution container
    from evedesign_server.backends.generic import execute_pipeline

    execute_pipeline(
        spec, job_id
    )

@app.function(
    image=inference_image,
    volumes={
        MODEL_PATH: model_volume,
    },
    cpu=1,
    memory=2048,
    timeout=24*60*60,
    secrets=[
        modal.Secret.from_name("db-connection-string"),
        modal.Secret.from_name("aws-credentials"),
    ],
)
def execute_pipeline_cpu(
    spec: PipelineSpec | SingleMutationScanSpec | ProteinToDnaSpec,
    job_id: str | UUID | None = None,
):
    """
    CPU-based execution of design pipeline via generic pipeline executor
    """
    # only import execute_pipeline here to keep dependency on evedesign
    # package limited to execution container
    from evedesign_server.backends.generic import execute_pipeline

    execute_pipeline(
        spec, job_id
    )

@app.function(
    image=api_image,
    secrets=[
        modal.Secret.from_name("supabase-access"),
        modal.Secret.from_name("db-connection-string"),
        modal.Secret.from_name("aws-credentials"),
        modal.Secret.from_name("other-settings"),
    ],
    min_containers=1,  # keep warm at all times
    max_containers=1,
    region="us-east-1",  # run in same region as supabase DB
)
@modal.concurrent(max_inputs=1000)
@modal.asgi_app()
# @modal.web_server(8000, startup_timeout=60)
def api():
    from evedesign_server.api.app import web_app
    return web_app

    # Note: --forwarded-allow-ips=* is discouraged as it allows to spoof headers
    # subprocess.Popen(
    #     'uvicorn evedesign_server.api.app:web_app --host 0.0.0.0 --port 8000 --log-level=info --proxy-headers --forwarded-allow-ips=*',
    #     shell=True
    # )

@app.function(
    image=api_image,
    schedule=modal.Period(seconds=600),
    secrets=[
        modal.Secret.from_name("db-connection-string"),
        modal.Secret.from_name("aws-credentials"),
    ],
    min_containers=0,  # keep warm at all times
    max_containers=1,
)
def balance_updater():
    from sqlalchemy.orm import Session
    from evedesign_server.db.engine import get_sync_engine
    from evedesign_server.db.controller import update_balance_for_all_sync

    eng = get_sync_engine()  # this gets connection string from environment var
    with (Session(eng) as session):
        update_balance_for_all_sync(session)
        session.commit()

@app.function(
    image=api_image,
    # schedule=modal.Period(seconds=60),
    secrets=[
        modal.Secret.from_name("db-connection-string"),
        modal.Secret.from_name("aws-credentials"),
    ],
    min_containers=0,  # keep warm at all times
    max_containers=1,
)
def queue_submitter():
    s_queue = Settings()
    from sqlalchemy.orm import Session
    from evedesign_server.db.engine import get_sync_engine
    from evedesign_server.db.controller import get_job_queue_entries_sync

    eng = get_sync_engine()  # this gets connection string from environment var
    with (Session(eng) as session):
        enqueued_jobs = get_job_queue_entries_sync(
            max_num_jobs=MAX_NUM_JOBS_PER_SUBMITTER_RUN, session=session
        )

        # iterate through jobs, commit them one by one and only submit after updating DB to avoid race conditions
        for entry in enqueued_jobs:
            job = entry.job
            logger.info(
                f"Processing queue entry {entry.id} for job {job.id} (created_at={entry.created_at}, received={entry.received})"
            )

            entry.received = True
            entry.job.status = "pending"
            session.commit()

            try:
                # note: we only load spec here because modal conveniently supports to
                # pass complex types to remote function calls, in other implementations
                # it is sufficient to pass the job ID to the standalone worker executable
                spec = load_spec_from_s3(job.id)

                if job.type == "pipeline" or job.type == "single_mutation_scan":
                    target_func = "execute_pipeline_gpu"
                else:
                    target_func = "execute_pipeline_cpu"

                process_job = modal.Function.from_name(
                    APP_NAME, target_func
                )

                call = process_job.spawn(
                    spec, job.id
                )
                modal_run_id = call.object_id
                entry.received = True
                update_job_in_place(
                    job,
                    session,
                    status="pending",
                    worker_name=s_queue.worker_name,
                    worker_job_id=modal_run_id,
                )
            except Exception:  # noqa
                # if submission failed, release job for resubmission
                logger.error(
                    f"Submission failed for queue entry {entry.id} / job {job.id} with exception {traceback.format_exc()}"
                )
                update_job_in_place(
                    job, session, status="initialized"
                )
                entry.received = False
            finally:
                session.commit()
