---
paths:
  - "migrations/**"
---

# Migrations

* Never edit a migration that has been applied. Add a new revision instead.
* Generate new revisions with `uv run alembic revision --autogenerate -m "<what changes>"`, then review the generated file before running `uv run alembic upgrade head`. Autogenerate misses renames (it emits a drop and an add) and some type changes, so fix those by hand.
* `migrations/env.py` sets `render_as_batch=True`, so autogenerate writes batch operations, which SQLite needs to alter tables. Keep them that way.
