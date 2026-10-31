from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL, IS_LOCAL_DB

_connect_args = {"options": "-c client_encoding=utf8"} if IS_LOCAL_DB else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency: one DB session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
