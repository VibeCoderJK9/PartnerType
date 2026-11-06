"""Initial isolated-producer schema, frozen from the final SQLite schema."""
from pathlib import Path
import json
from alembic import op
from sqlalchemy import text

revision = '0001_postgres'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    sql = Path(__file__).with_name('0001_schema.sql').read_text()
    for statement in sql.split(';'):
        if statement.strip():
            connection.exec_driver_sql(statement)
    for row in json.loads(Path(__file__).parents[1].joinpath('default_stages.json').read_text()):
        row.pop('id', None)
        columns = ','.join(row)
        values = ','.join(':'+key for key in row)
        connection.execute(text(f'INSERT INTO lead_stages ({columns}) VALUES ({values})'), row)
    connection.execute(text("INSERT INTO settings(key,value) VALUES ('seed_factory_defaults','0')"))


def downgrade():
    raise RuntimeError('Destructive downgrade forbidden. Restore a backup into a new instance.')
