"""
Database connection functionality to be used outside of API
(e.g. pipeline uploading results to DB)

Usage:
    with Session(engine) as session:
        ...
        session.commit()
"""
from sqlalchemy import create_engine
from evedesign_server.db.models import Base
from evedesign_server.config import Settings

def get_sync_engine(connection_string: str | None = None):
    """
    Connect to database using SQLAlchemy
    """
    s = Settings()
    database_url = connection_string or s.db_connection_string_sync
    if database_url is None:
        raise ValueError(
            f"Must specify database connection string in settings"
        )

    engine = create_engine(database_url)
    if s.db_iam_auth:
        from evedesign_server.db.iam import attach_rds_iam_auth
        attach_rds_iam_auth(engine, region=s.aws_region_name)

    return engine

# @contextmanager
# def get_sync_session(connection_string: str | None = None):
#    yield Session(get_sync_engine(connection_string))

def create_all(connection_string: str | None = None):
    engine = get_sync_engine(connection_string)
    # exclude users table which is a supabase locked table
    Base.metadata.create_all(
        engine,
        tables=[t for t in Base.metadata.sorted_tables if t.name != "users"]
    )

def drop_all(connection_string: str | None = None):
    engine = get_sync_engine(connection_string)
    # exclude users table which is a supabase locked table
    Base.metadata.drop_all(
        engine, tables=[t for t in Base.metadata.sorted_tables if t.name != "users"]
    )