from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from .config import settings
engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
if engine.dialect.name == 'sqlite':
    @event.listens_for(engine, 'connect')
    def sqlite_fk(conn, record): conn.execute('PRAGMA foreign_keys=ON')
Session = sessionmaker(engine, expire_on_commit=False)
def get_db():
    with Session() as db: yield db
