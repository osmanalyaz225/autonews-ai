from urllib.parse import urlparse
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings

url = settings.database_url
parsed = urlparse(url.replace("postgresql://", "postgresql+psycopg://", 1) if url.startswith("postgresql://") else url)

# Render can expose an incomplete database URL during setup. Never crash the site
# on boot in that case; use the local SQLite store until a complete URL is supplied.
if parsed.scheme.startswith("postgresql") and not parsed.password:
    url = "sqlite:///./data/autonews.db"
elif url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def init_db():
    from . import models
    Base.metadata.create_all(bind=engine)
