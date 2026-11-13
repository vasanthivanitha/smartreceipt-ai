"""Opt-in full flow using the configured DB, real SMTP, Redis/RQ, S3 and Tesseract.
Creates a synthetic account, then deletes its receipt. Never prints tokens/secrets.
Run: docker compose exec web python -m tests.integration_smoke
"""
import os,re,time,uuid
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from redis import Redis
from rq import SimpleWorker,Queue
from sqlalchemy import select
from app.main import app
from app.config import settings
from app.db import Session
from app.models import User,Membership,Category,Deletion
from app.jobs import sweep

def main():
    email=f'smoke-{uuid.uuid4().hex[:12]}@example.test'
    mailpit=os.getenv('SMOKE_MAILPIT_URL','http://mailpit:8025')
    with TestClient(app) as client:
        def csrf(path='/auth/signup'):
            return re.search(r'name="csrf-token" content="([^"]+)"',client.get(path).text)[1]
        result=client.post('/auth/signup',data={'csrf':csrf(),'email':email,'password':'synthetic-smoke-password','name':'Smoke workspace'})
        assert result.status_code==200
        for _ in range(20):
            messages=httpx.get(mailpit+'/api/v1/messages').json()['messages']
            match=next((m for m in messages if any(recipient['Address']==email for recipient in m['To'])),None)
            if match: break
            time.sleep(.25)
        assert match,'Verification email did not arrive'
        text=httpx.get(mailpit+'/api/v1/message/'+match['ID']).json()['Text']
        token=re.search(r'/auth/verify\?token=([\w-]+)',text)[1]
        assert client.post('/auth/verify',data={'csrf':csrf(),'token':token}).status_code==200
        assert client.post('/auth/login',data={'csrf':csrf(),'email':email,'password':'synthetic-smoke-password'},follow_redirects=False).status_code==303
        token=csrf('/receipts');headers={'X-CSRF-Token':token}
        data=Path('samples/synthetic-receipt.png').read_bytes()
        uploaded=client.post('/api/upload',files={'file':('synthetic.png',data,'image/png')},headers=headers)
        assert uploaded.status_code==200,uploaded.text
        rid=uploaded.json()['id']
        repeated=client.post('/api/upload',files={'file':('synthetic.png',data,'image/png')},headers=headers)
        assert repeated.json()['duplicate'] and repeated.json()['id']==rid
        sweep()
        connection=Redis.from_url(settings.redis_url)
        SimpleWorker([Queue('receipts',connection=connection)],connection=connection).work(burst=True,logging_level='CRITICAL')
        review=client.get(f'/receipts/{rid}')
        assert review.status_code==200 and 'needs_review' not in review.url.path
        with Session() as db:
            user=db.scalar(select(User).where(User.email==email))
            membership=db.scalar(select(Membership).where(Membership.user_id==user.id))
            wid=membership.workspace_id
            cats={c.name:c.id for c in db.scalars(select(Category).where(Category.workspace_id==wid))}
        payload=dict(store='Greenfield Market',purchase_date='2026-11-01',currency='USD',subtotal='18.50',tax='1.50',discount='0.00',total='20.00',items=[
            dict(name='Milk',quantity='2',unit_price='2.25',line_total='4.50',category_id=cats['Groceries']),
            dict(name='Paper towels',quantity='1',unit_price='6.00',line_total='6.00',category_id=cats['Household']),
            dict(name='Shampoo',quantity='1',unit_price='8.00',line_total='8.00',category_id=cats['Personal Care'])])
        saved=client.post(f'/api/receipts/{rid}/confirm',json=payload,headers=headers)
        assert saved.status_code==200,saved.text
        assert '20.00' in client.get('/reports?currency=USD').text
        assert 'Milk,Groceries' in client.get('/export.csv?kind=items').text
        assert client.get(f'/receipts/{rid}/source').content==data
        assert client.post(f'/receipts/{rid}/delete',data={'csrf':token},follow_redirects=False).status_code==303
        time.sleep(1.1);sweep()
        with Session() as db: assert not db.scalar(select(Deletion).where(Deletion.workspace_id==wid))
    print('PASS: real SMTP verification → auth → private S3 upload → duplicate → Redis/RQ OCR → review → confirm → reports/export → download → deletion cleanup')
if __name__=='__main__': main()
