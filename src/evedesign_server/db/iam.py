"""
Optional AWS RDS/Aurora IAM database authentication.

Enable with DESIGNSERVER_DB_IAM_AUTH=true and a connection string without password,
e.g. postgresql://evedesign_api@mydb.xxxx.eu-central-1.rds.amazonaws.com:5432/postgres

A fresh auth token (valid for 15 minutes) is generated locally for every new connection
the pool opens. Credentials come from the default boto3 chain (EKS IRSA/Pod Identity,
AWS Batch job role, AWS_* env vars). TLS is configured through the standard libpq
environment variables, honoured by both psycopg and asyncpg:
PGSSLMODE=verify-full PGSSLROOTCERT=/path/to/rds-global-bundle.pem
"""
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine


def attach_rds_iam_auth(engine: Engine | AsyncEngine, region: str | None = None) -> None:
    """Use short-lived IAM tokens as database password for all new connections of engine."""
    import boto3  # only required when IAM auth is enabled

    sync_engine = engine.sync_engine if isinstance(engine, AsyncEngine) else engine
    url = sync_engine.url
    rds = boto3.client("rds", region_name=region)  # None -> default chain (e.g. AWS_REGION)

    @event.listens_for(sync_engine, "do_connect")
    def _inject_token(dialect, conn_rec, cargs, cparams):  # noqa
        cparams["password"] = rds.generate_db_auth_token(
            DBHostname=url.host, Port=url.port or 5432,
            DBUsername=url.username, Region=rds.meta.region_name,
        )
