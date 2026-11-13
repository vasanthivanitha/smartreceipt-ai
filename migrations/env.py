from alembic import context
from app.db import engine
from app.models import Base
if context.is_offline_mode():
    context.configure(url=str(engine.url),target_metadata=Base.metadata,literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    with engine.connect() as conn:
        context.configure(connection=conn,target_metadata=Base.metadata,compare_type=True)
        with context.begin_transaction(): context.run_migrations()
