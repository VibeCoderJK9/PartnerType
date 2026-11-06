from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from api.config import secret

engine = create_engine(secret('MIGRATION_DATABASE_URL').replace('postgresql://', 'postgresql+psycopg://', 1), poolclass=NullPool)
with engine.connect() as connection:
    # Session lock covers Alembic version discovery, all DDL and version writes.
    connection.exec_driver_sql('SELECT pg_advisory_lock(734102619)')
    connection.commit()
    try:
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.exec_driver_sql('SELECT pg_advisory_unlock(734102619)')
        connection.commit()
