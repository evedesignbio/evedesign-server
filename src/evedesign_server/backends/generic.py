import json
import traceback
from pathlib import Path
from uuid import UUID

import click
from loguru import logger
import boto3

from evedesign_server.schemas import PipelineSpec, ProteinToDnaSpec, SingleMutationScanSpec, InputSpecAdapter
from evedesign_server.db.controller import spec_path_for_job_id
from evedesign_server.config import Settings


def execute_pipeline(
    spec: PipelineSpec | SingleMutationScanSpec | ProteinToDnaSpec,
    job_id: str | UUID | None = None
) -> dict:
    # note that following imports require evedesign package to be present so carefully import this file
    from evedesign_server.pipeline import execute, protein_to_dna, single_mutation_scan, filter_a3m
    from evedesign_server.db.tracker import Tracker

    logger.info(f"Started Job ID: {job_id}")

    # instantiate tracker (if connection string/AWS credentials are None, tracker will do nothing)
    tracker = Tracker(
        job_id=job_id
    )

    tracker.update(
        status="running"
    )

    try:
        # until we have a cleaner solution filter a3m before we go into pipeline
        if isinstance(spec, PipelineSpec) or isinstance(spec, SingleMutationScanSpec):
            spec = filter_a3m(spec)

        # execute the pipeline run
        if isinstance(spec, PipelineSpec):
            result, updated_pipeline_spec = execute(spec, job_id)

            # merge result and pipeline spec together for storing
            result = {
                "spec": updated_pipeline_spec,
                "instances": result,
            }
        elif isinstance(spec, SingleMutationScanSpec):
            result, updated_single_spec = single_mutation_scan(spec, job_id)
            # merge result and pipeline spec together for storing
            result = {
                "spec": updated_single_spec,
                "scores": result,
            }
        elif isinstance(spec, ProteinToDnaSpec):
            result = protein_to_dna(spec)
            # merge result and protein to dna spec together for storing
            result = {
                "spec": spec,
                "dna_sequences": result,
            }
        else:
            raise ValueError("Invalid spec type")

        # store results to global result store
        tracker.update(
            status="finished", results=result
        )

    except Exception:
        tracker.update(
            status="failed", message=traceback.format_exc()
        )

        raise

    return result

def load_spec_from_s3(job_id: UUID):
    s = Settings()
    logger.info(f"Retrieving spec from S3 for job id {job_id}")

    s3 = boto3.client(
        "s3",
        region_name=s.aws_region_name,
        aws_access_key_id=s.aws_access_key_id,
        aws_secret_access_key=s.aws_secret_access_key,
    )

    spec_json = json.load(
        s3.get_object(
            Bucket=s.aws_s3_bucket_name, Key=spec_path_for_job_id(job_id)
        )["Body"]
    )

    # deserialize input spec
    spec = InputSpecAdapter.validate_python(spec_json)

    return spec

def load_spec_from_file(spec_file_path: Path):
    with open(spec_file_path) as f:
        spec_json = json.load(f)

    # deserialize input spec
    return InputSpecAdapter.validate_python(spec_json)

@click.group()
def cli():
    pass

@cli.command()
def setup():
    from evedesign_server.utils import download_model_files
    download_model_files()

@cli.command()
@click.argument(
    "job_id", type=click.UUID
)
def run(job_id):
    execute_pipeline(
        load_spec_from_s3(job_id), job_id
    )

@cli.command()
@click.argument(
    "input_spec_path",
    required=True,
    type=click.Path(
        exists=True, file_okay=True, dir_okay=False, path_type=Path
    )
)
@click.argument(
    "result_file_path",
    required=True,
    type=click.Path(path_type=Path)
)
@click.argument(
    "job_id",
    type=click.UUID,
    required=False,
)
def runfile(input_spec_path: Path, result_file_path: Path, job_id: UUID | None):
    from evedesign_server.db.tracker import serialize_results
    res = execute_pipeline(
        load_spec_from_file(input_spec_path), job_id
    )

    res_json = serialize_results(res)
    with open(result_file_path, "w") as f:
        json.dump(res_json, f)  # noqa

if __name__ == "__main__":
    cli()
