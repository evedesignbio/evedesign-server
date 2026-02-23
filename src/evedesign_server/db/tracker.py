from io import BytesIO
import json
import signal
from typing import Any
from uuid import UUID
import boto3
from sqlalchemy.orm import Session
from evedesign_server.db.controller import get_job_by_id_sync, update_job_in_place, result_path_for_job_id
from evedesign_server.db.engine import get_sync_engine
from evedesign_server.db.models import JobStatus
from evedesign_server.config import Settings

def serialize_results(results: dict[str, Any]):
    return {
        k: (
            [e.model_dump() for e in v]
            if isinstance(v, list)
            else v.model_dump()
        ) for k, v in results.items()
    }

class Tracker:
    """
    Track job results in database
    # TODO: implement retry behaviour
    """
    def __init__(
        self,
        job_id: UUID | None
    ):
        self.settings = Settings()
        self.job_id = job_id
        self._terminated = False  # potentially modify update behaviour in light of upcoming termination

        signal.signal(signal.SIGINT, self.handle_termination)
        signal.signal(signal.SIGTERM, self.handle_termination)
        signal.signal(signal.SIGUSR1, self.handle_termination)
        signal.signal(signal.SIGUSR2, self.handle_termination)

    def handle_termination(self, sig, frame):
        self._terminated = True
        self.update(status="terminated")

    def update(
        self,
        status: JobStatus | None = None,
        results: dict[str, Any] | None = None,
        message: str | None = None,
        worker_name: str | None = None,
        worker_job_id: str | None = None,
    ):
        # if no connection string specified, do nothing
        # update results if we have S3 bucket settings in place
        if (
            self.job_id is None or
            self.settings.db_connection_string_sync is None or
            self.settings.aws_region_name is None or
            self.settings.aws_access_key_id is None or
            self.settings.aws_secret_access_key is None or
            self.settings.aws_s3_bucket_name is None
        ):
            return

        # with get_sync_session(connection_string=self.connection_string) as session:
        eng = get_sync_engine(connection_string=self.settings.db_connection_string_sync)
        with Session(eng) as session:
            # fail if invalid job ID
            job = get_job_by_id_sync(self.job_id, session=session)
            update_job_in_place(
                job,
                session,
                status=status,
                spec=results.get("spec") if results is not None else None,
                message=message,
                worker_name=worker_name,
                worker_job_id=worker_job_id
            )

            # commit all changes
            session.commit()

        if results is not None:
            results_json = serialize_results(results)

            results_dump = json.dumps(results_json).encode()
            s3 = boto3.client(
                "s3",
                region_name=self.settings.aws_region_name,
                aws_access_key_id=self.settings.aws_access_key_id,
                aws_secret_access_key=self.settings.aws_secret_access_key,
            )

            s3.upload_fileobj(
                BytesIO(results_dump),
                self.settings.aws_s3_bucket_name,
                result_path_for_job_id(self.job_id)
            )
