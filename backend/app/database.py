"""
Setu backend — database connection.

Sets up a SQLAlchemy engine pointed at Supabase Postgres, and a
session factory every router/service will use to talk to the database.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import get_settings

settings = get_settings()

# Supabase gives you a connection string starting with "postgresql://",
# which SQLAlchemy interprets as "use the psycopg2 driver" by default.
# We're using psycopg (v3) instead, so we rewrite the prefix here rather
# than requiring you to hand-edit the URL Supabase gives you every time.
db_url = settings.database_url
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

# pool_pre_ping checks a connection is still alive before using it —
# worth having since Supabase's free-tier project can pause/idle,
# and this avoids mysterious "connection closed" errors after inactivity.
engine = create_engine(db_url, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """
    FastAPI dependency — yields a database session per request,
    and always closes it afterward, even if the request errors.
    Usage in a router: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()