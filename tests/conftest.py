import os
os.environ.setdefault('DATABASE_URL','sqlite://')
os.environ.setdefault('SECURE_COOKIE','false')
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.models import Base,User
from app import db as database, services, storage
from app.main import app
from fastapi.testclient import TestClient

@pytest.fixture
def db_factory(monkeypatch):
    url=os.getenv('TEST_DATABASE_URL','sqlite://')
    kwargs={'poolclass':StaticPool,'connect_args':{'check_same_thread':False}} if url=='sqlite://' else {}
    engine=create_engine(url,**kwargs)
    if engine.dialect.name=='sqlite':
        from sqlalchemy import event
        @event.listens_for(engine,'connect')
        def fk(conn,_): conn.execute('PRAGMA foreign_keys=ON')
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    factory=sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr(database,'Session',factory)
    from app import jobs
    monkeypatch.setattr(jobs,"Session",factory)
    def dependency():
        with factory() as session: yield session
    app.dependency_overrides[database.get_db]=dependency
    yield factory
    app.dependency_overrides.clear(); Base.metadata.drop_all(engine); engine.dispose()

@pytest.fixture
def tenants(db_factory):
    from app.security import hash_password
    with db_factory() as db:
        users=[]; workspaces=[]
        for n in range(2):
            u=User(email=f'customer{n}@example.test',password=hash_password('synthetic-password'),verified=True)
            db.add(u); db.flush(); w=services.create_workspace(db,u,f'Workspace {n}')
            users.append(u); workspaces.append(w)
        db.commit()
    return users,workspaces

@pytest.fixture
def memory_storage(monkeypatch):
    objects={}
    monkeypatch.setattr(storage,'put',lambda key,data,mime:objects.__setitem__(key,data))
    monkeypatch.setattr(storage,'read',lambda key:objects[key])
    monkeypatch.setattr(storage,'remove',lambda key:objects.pop(key,None))
    return objects

@pytest.fixture
def clients(db_factory,tenants,monkeypatch):
    from app import security
    monkeypatch.setattr(security,'rate_limit',lambda *a,**kw:None)
    result=[]
    for u in tenants[0]:
        c=TestClient(app); c.get('/')
        token=c.get('/').text.split('name="csrf-token" content="')[1].split('"')[0]
        response=c.post('/auth/login',data={'email':u.email,'password':'synthetic-password','csrf':token},follow_redirects=False)
        assert response.status_code==303
        result.append(c)
    yield result
    for c in result: c.close()

def csrf(client):
    return client.get('/receipts').text.split('name="csrf-token" content="')[1].split('"')[0]
