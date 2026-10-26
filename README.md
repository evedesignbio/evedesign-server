# evedesign_server

[![PyPI - Version](https://img.shields.io/pypi/v/evedesign_server.svg)](https://pypi.org/project/evedesign_server)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/evedesign_server.svg)](https://pypi.org/project/evedesign_server)
-----
## Scope

*evedesign_server* is a lightweight pipeline execution runner and REST API built on top of the *evedesign* package. 
It allows to execute design pipelines from a single specification file either locally or through a REST API with 
additional functions for job status and result retrieval. 

Note: job execution from the job queue must be implemented by a custom piece of glue code that retrieves jobs from
the queue and submits it to the respective execution environment.

## Setup

### Python package installation
```console
pip install evedesign_server[api,pipeline]
```
This installs the full version of the package with all functionality.
* The ```api``` option is required to host the REST API 
* The ```pipeline``` option is required to execute design pipelines

You can leave away either option if the functionality is not needed. 
Installing without options gives a lightweight version of the package for data validation purpose only.

### Configuration for API and pipeline runner

Settings are managed with Pydantic Settings in [config.py](src/evedesign_server/config.py). 
The following settings need to be defined with environment variables for the REST API and tracking job results during
execution (not needed for local execution without job status tracking):

```console
# connection string of PostgreSQL database for tracking job metadata and job queue
DESIGNSERVER_DB_CONNECTION_STRING=postgresql://...

# arbitrary unique name to identify the current worker
DESIGNSERVER_WORKER_NAME=

# S3 bucket for storing input specification and result files
DESIGNSERVER_AWS_ACCESS_KEY_ID=
DESIGNSERVER_AWS_SECRET_ACCESS_KEY=
DESIGNSERVER_AWS_REGION_NAME=
DESIGNSERVER_AWS_S3_BUCKET_NAME=

# supabase URL/public key for user authentication
DESIGNSERVER_SUPABASE_URL=
DESIGNSERVER_SUPABASE_ANON_KEY=

```
### Database setup

Run the following command once to prepare all required database tables (requires `DESIGNSERVER_DB_CONNECTION_STRING`to be set, cf. above):
```console
python -m evedesign_server.db.cli create-all
```

### Model files

Model files need to be downloaded once before running any design jobs. Execute the `setup` command with the Docker container
or `python -m evedesign_server.backends.generic setup`. Model files will be stored to MODEL_PARAM_ROOT (cf. [config.py](src/evedesign_server/config.py), */models* by default,
overwrite the environment variable to choose a different directory or bind it to a directory when executing through Docker).  

## Job execution

### Input specification

Depending on job type, instantiate one of the following Pydantic schemas, serialize it
to JSON with model_dump() and store it as a file; cf. [schemas.py](src/evedesign_server/schemas.py):
* PipelineSpec
* SingleMutationScanSpec 
* ProteinToDnaSpec

### Running design jobs

Jobs can be executed from the command line either by  
* building the Docker image and running it
* running ```python -m evedesign_server.backends.generic```

Both receive the same arguments as detailed in the next section.

#### Execution by job ID

This mode will fetch the input specification JSON file from the S3 bucket (*specs/<job_id>*) and store the results there as well (*results/<job_id*).
If the database connection string is specified, the job status will be tracked in the database.

```console
run <job_id>
```

#### Execution from local file

This mode will execute a local specification file (JSON format) and also store the results locally. If job_id
and the database connection string are specified, the job status will be tracked in the database. 

```console
run <input_spec_path> <result_file_path> [<job_id>]
```

## License

evedesign_server is released under the MIT license.

## Contact

For general questions or inquiries about *evedesign* please reach out to [hello@evedesign.bio](mailto:hello@evedesign.bio).