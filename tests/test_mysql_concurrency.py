"""These tests require a disposable MySQL database; SQLite cannot prove row locking."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sqlalchemy import select,func
import pytest
from fastapi import HTTPException
from app import services
from app.models import Usage,Receipt
from app.config import settings
pytestmark=pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL','').startswith('mysql'),reason='Requires disposable MySQL test database')

def test_concurrent_limit(db_factory,tenants,memory_storage,monkeypatch):
    wid=tenants[1][0].id; monkeypatch.setattr(settings,'free_limit',1)
    original=Path('samples/synthetic-text.pdf').read_bytes()
    def upload(n):
        with db_factory() as db:
            try:
                receipt,duplicate=services.upload(db,wid,original+f'\n% synthetic variant {n}'.encode()); db.commit(); return True
            except HTTPException as e:
                db.rollback(); assert e.status_code==403; return False
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(upload,range(4)))
    assert sum(results)==1
    with db_factory() as db:
        assert db.get(Usage,(wid,services.month())).used==1
        assert db.scalar(select(func.count()).select_from(Receipt))==1

def test_concurrent_identical_file(db_factory,tenants,memory_storage):
    wid=tenants[1][0].id; data=Path('samples/synthetic-receipt.png').read_bytes()
    def upload(_):
        with db_factory() as db:
            r,duplicate=services.upload(db,wid,data); db.commit(); return r.id,duplicate
    with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(upload,range(4)))
    assert len({r[0] for r in results})==1
    assert sum(not r[1] for r in results)==1
    with db_factory() as db: assert db.get(Usage,(wid,services.month())).used==1
