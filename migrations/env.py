"""Alembic runs this file. It migrates the database named by ORDERS_DATABASE_URL."""

from alembic import context
from sqlalchemy import create_engine

import orders.models  # noqa: F401  (registers the tables on Base.metadata)
from orders import settings
from orders.db import Base

engine = create_engine(settings.DATABASE_URL)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
