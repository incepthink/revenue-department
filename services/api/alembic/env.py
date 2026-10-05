from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from rci.config import get_settings
from rci.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("url") or config.get_main_option("sqlalchemy.url") or get_settings().database_url


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in (None, "public", "vault")
    return True


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, include_schemas=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
