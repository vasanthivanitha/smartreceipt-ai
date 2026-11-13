import json, time, uuid
from datetime import datetime, timedelta
from sqlalchemy import select
from redis import Redis
from rq import Queue, Retry
from .db import Session
from .models import Receipt, Deletion
from .services import lock_workspace, release
from . import storage
from .config import settings
from .ocr import extract_text, parse

def process_receipt(wid,rid):
    token=str(uuid.uuid4())
    with Session() as db:
        lock_workspace(db,wid)
        r=db.scalar(select(Receipt).where(Receipt.workspace_id==wid,Receipt.id==rid).with_for_update())
        if not r or r.state in ('confirmed','needs_review','failed'): return
        if r.state=='processing' and r.updated_at>datetime.utcnow()-timedelta(minutes=10): return
        if r.attempts>=3:
            r.state='failed'; release(db,r); db.commit(); return
        r.state='processing'; r.job_token=token; r.updated_at=datetime.utcnow(); r.attempts+=1
        key,mime=r.object_key,r.mime; db.commit()
    try:
        draft,flags=parse(extract_text(storage.read(key),mime))
        with Session() as db:
            lock_workspace(db,wid)
            r=db.scalar(select(Receipt).where(Receipt.workspace_id==wid,Receipt.id==rid).with_for_update())
            if not r or r.job_token!=token or r.state!='processing': return
            r.draft=json.dumps(draft); r.flags=json.dumps(flags); r.store_guess=draft['store']
            r.state='needs_review'; r.updated_at=datetime.utcnow(); db.commit()
    except Exception:
        # Never log OCR text, file contents or provider exception details.
        with Session() as db:
            lock_workspace(db,wid)
            r=db.scalar(select(Receipt).where(Receipt.workspace_id==wid,Receipt.id==rid).with_for_update())
            if r and r.job_token==token and r.state=='processing':
                r.state='failed' if r.attempts>=3 else 'queued'
                r.updated_at=datetime.utcnow()
                if r.state=='failed': release(db,r)
                db.commit()
        raise RuntimeError('Receipt processing failed; content withheld') from None

def sweep():
    # DB queued records form a durable outbox. Duplicate dispatches are safe.
    queue=Queue('receipts',connection=Redis.from_url(settings.redis_url))
    with Session() as db:
        pending=list(db.execute(select(Receipt.workspace_id,Receipt.id).where(
            (Receipt.state=='queued') | ((Receipt.state=='processing') & (Receipt.updated_at<datetime.utcnow()-timedelta(minutes=10))))))
    for wid,rid in pending:
        queue.enqueue("app.jobs.process_receipt",wid,rid,job_timeout=540,result_ttl=0,failure_ttl=3600)
    with Session() as db:
        ids=list(db.scalars(select(Deletion.id).where(Deletion.next_attempt<=datetime.utcnow())))
    for did in ids:
        with Session() as db:
            d=db.scalar(select(Deletion).where(Deletion.id==did).with_for_update())
            if not d or d.next_attempt>datetime.utcnow(): continue
            try: storage.remove(d.object_key)
            except Exception:
                d.attempts+=1; d.next_attempt=datetime.utcnow()+timedelta(seconds=min(86400,30*2**min(d.attempts,12)))
            else: db.delete(d)
            db.commit()

if __name__=='__main__':
    while True:
        try: sweep()
        except Exception: print('Maintenance unavailable; will retry.',flush=True)
        time.sleep(30)
