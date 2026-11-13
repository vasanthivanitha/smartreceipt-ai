from datetime import datetime,timedelta
from pathlib import Path
from sqlalchemy import select,func
from app import jobs,services,storage
from app.models import Receipt,Usage,Deletion
import pytest

def accepted(db_factory,wid):
    with db_factory() as db:
        r,_=services.upload(db,wid,Path('samples/synthetic-receipt.png').read_bytes());db.commit();return r

def test_stale_worker_is_fenced(db_factory,tenants,memory_storage,monkeypatch):
    wid=tenants[1][0].id;r=accepted(db_factory,wid)
    with db_factory() as db:
        row=db.get(Receipt,r.id);row.state='processing';row.job_token='old';row.attempts=3
        row.updated_at=datetime.utcnow()-timedelta(minutes=11);db.commit()
    jobs.process_receipt(wid,r.id)
    with db_factory() as db:
        assert db.get(Receipt,r.id).state=='failed'
        assert db.get(Usage,(wid,services.month())).used==0

def test_wrong_workspace_job_cannot_read_file(db_factory,tenants,memory_storage,monkeypatch):
    r=accepted(db_factory,tenants[1][0].id)
    def forbidden(*a): pytest.fail('Cross-workspace job read a file')
    monkeypatch.setattr(storage,'read',forbidden)
    jobs.process_receipt(tenants[1][1].id,r.id)
    with db_factory() as db: assert db.get(Receipt,r.id).state=='queued'

def test_deletion_failure_retries_durably(db_factory,tenants,memory_storage,monkeypatch):
    wid=tenants[1][0].id;r=accepted(db_factory,wid)
    with db_factory() as db:
        services.delete_receipt(db,wid,r.id);db.commit()
        d=db.scalar(select(Deletion).where(Deletion.object_key==r.object_key))
        d.next_attempt=datetime.utcnow()-timedelta(seconds=2);db.commit()
    def fail(*a): raise OSError('Synthetic unavailable object store')
    monkeypatch.setattr(storage,'remove',fail);jobs.sweep()
    with db_factory() as db:
        d=db.scalar(select(Deletion).where(Deletion.object_key==r.object_key))
        assert d.attempts==1 and d.next_attempt>datetime.utcnow()
        d.next_attempt=datetime.utcnow()-timedelta(seconds=1);db.commit()
    monkeypatch.setattr(storage,'remove',lambda key:memory_storage.pop(key,None));jobs.sweep()
    with db_factory() as db: assert db.scalar(select(func.count()).select_from(Deletion))==0
    assert r.object_key not in memory_storage
