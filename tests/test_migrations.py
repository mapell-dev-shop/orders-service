import os
import subprocess
import sys
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from orders import models  # noqa: F401
from orders.db import Base


def test_migrations_build_exactly_the_tables_the_models_describe(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'orders.db'}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={**os.environ, "ORDERS_DATABASE_URL": url},
        check=True,
    )

    with create_engine(url).connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert differences == []
