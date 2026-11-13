import json
from pathlib import Path
from decimal import Decimal
import pytest
from sqlalchemy import select,func
from fastapi import HTTPException
from app import services,jobs,storage
from app.models import Receipt,Item,Category,Usage,Deletion
from .conftest import csrf

SAMPLE=Path('samples/synthetic-receipt.png').read_bytes()
def create(db_factory,wid):
    with db_factory() as db:
        r,dup=services.upload(db,wid,SAMPLE); db.commit(); return r

def draft_payload(db_factory,wid):
    with db_factory() as db:
        cats={c.name:c.id for c in db.scalars(select(Category).where(Category.workspace_id==wid))}
    return dict(store='Greenfield Market',purchase_date='2026-11-01',currency='USD',subtotal='18.50',tax='1.50',discount='0.00',total='20.00',items=[
        dict(name='Milk',quantity='2',unit_price='2.25',line_total='4.50',category_id=cats['Groceries']),
        dict(name='Paper towels',quantity='1',unit_price='6.00',line_total='6.00',category_id=cats['Household']),
        dict(name='Shampoo',quantity='1',unit_price='8.00',line_total='8.00',category_id=cats['Personal Care'])])

def test_isolation_all_paths(db_factory,tenants,memory_storage,clients):
    w1,w2=tenants[1]; a=create(db_factory,w1.id); b=create(db_factory,w2.id)
    assert a.sha256==b.sha256 and a.object_key!=b.object_key
    c1,c2=clients
    for client,other in [(c1,b),(c2,a)]:
        for path in [f'/receipts/{other.id}',f'/receipts/{other.id}/source',f'/receipts/{other.id}/source?download=true']:
            assert client.get(path).status_code==404
        headers={'X-CSRF-Token':csrf(client)}
        assert client.post(f'/api/receipts/{other.id}/confirm',json={},headers=headers).status_code==404
        assert client.post(f'/receipts/{other.id}/delete',data={'csrf':headers['X-CSRF-Token']}).status_code==404
        assert str(other.id) not in [line.split(',')[0] for line in client.get('/export.csv').text.splitlines()[1:]]
    with db_factory() as db:
        cat=db.scalar(select(Category).where(Category.workspace_id==w1.id))
    assert c2.post('/categories',data={'csrf':csrf(c2),'category_id':cat.id,'name':'Intrusion'}).status_code==404

def test_confirm_atomic_and_reports(db_factory,tenants,memory_storage,clients,monkeypatch):
    wid=tenants[1][0].id; r=create(db_factory,wid)
    monkeypatch.setattr(jobs,'extract_text',lambda *a:'GREENFIELD MARKET\n2026-11-01\nUSD\nMIlk 4.50\nPaper towels 6.00\nShampoo 8.00\nSubtotal 18.50\nTax 1.50\nDiscount 0.00\nTotal 20.00')
    jobs.process_receipt(wid,r.id); jobs.process_receipt(wid,r.id)
    payload=draft_payload(db_factory,wid); c=clients[0]; headers={'X-CSRF-Token':csrf(c)}
    bad={**payload,'total':'99'}
    assert c.post(f'/api/receipts/{r.id}/confirm',json=bad,headers=headers).status_code==409
    assert c.post(f'/api/receipts/{r.id}/confirm',json={**bad,'accept_discrepancy':'false'},headers=headers).status_code==409
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Item))==0
        assert db.get(Receipt,r.id).state=='needs_review'
    assert c.post(f'/api/receipts/{r.id}/confirm',json=payload,headers=headers).status_code==200
    assert c.post(f'/api/receipts/{r.id}/confirm',json=payload,headers=headers).status_code==200
    jobs.process_receipt(wid,r.id)
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Item))==3
        assert db.get(Usage,(wid,services.month())).used==1
    report=c.get('/reports?currency=USD').text
    assert '20.00' in report and '4.50' in report and '6.00' in report and '8.00' in report
    assert '20.00' not in c.get('/reports?currency=EUR').text
    csv=c.get('/export.csv?kind=items').text
    assert 'Milk,Groceries,2.000,2.25,4.50' in csv
    assert c.get(f'/receipts/{r.id}').status_code==200

def test_hash_duplicate_does_not_use_quota(db_factory,tenants,memory_storage):
    wid=tenants[1][0].id; r=create(db_factory,wid)
    with db_factory() as db:
        repeated,duplicate=services.upload(db,wid,SAMPLE); db.commit()
        assert duplicate and repeated.id==r.id
        assert db.get(Usage,(wid,services.month())).used==1
        assert db.scalar(select(func.count()).select_from(Receipt))==1

def test_category_foreign_key_blocks_cross_tenant(db_factory,tenants,memory_storage):
    from sqlalchemy.exc import IntegrityError
    w1,w2=tenants[1]; r=create(db_factory,w1.id)
    with db_factory() as db:
        c=db.scalar(select(Category).where(Category.workspace_id==w2.id))
        db.add(Item(workspace_id=w1.id,receipt_id=r.id,category_id=c.id,name='bad',line_total=1))
        with pytest.raises(IntegrityError): db.commit()

def test_failed_processing_releases_once(db_factory,tenants,memory_storage,monkeypatch):
    wid=tenants[1][0].id; r=create(db_factory,wid)
    def fail(*args): raise ValueError('Sensitive OCR text')
    monkeypatch.setattr(jobs,'extract_text',fail)
    for _ in range(3):
        with pytest.raises(RuntimeError,match='content withheld'): jobs.process_receipt(wid,r.id)
    jobs.process_receipt(wid,r.id)
    with db_factory() as db:
        assert db.get(Receipt,r.id).state=='failed'
        assert db.get(Usage,(wid,services.month())).used==0
        assert db.get(Receipt,r.id).attempts==3

def test_possible_duplicate_requires_confirmation(db_factory,tenants,memory_storage):
    wid=tenants[1][0].id; r=create(db_factory,wid); p=draft_payload(db_factory,wid)
    with db_factory() as db:
        db.get(Receipt,r.id).state='needs_review'; services.confirm(db,wid,r.id,p); db.commit()
        b=Receipt(workspace_id=wid,sha256='a'*64,object_key='other',mime='image/png',quota_month=services.month(),state='needs_review'); db.add(b); db.commit()
        with pytest.raises(HTTPException) as err: services.confirm(db,wid,b.id,p)
        assert err.value.status_code==409; db.rollback()
        services.confirm(db,wid,b.id,{**p,'distinct_purchase':True}); db.commit()

def test_delete_creates_durable_cleanup(db_factory,tenants,memory_storage):
    wid=tenants[1][0].id; r=create(db_factory,wid)
    with db_factory() as db:
        services.delete_receipt(db,wid,r.id); db.commit()
        assert db.get(Receipt,r.id) is None
        assert db.scalar(select(Deletion).where(Deletion.object_key==r.object_key)) is not None
        assert db.get(Usage,(wid,services.month())).used==0

def test_browser_csrf_and_file_validation(db_factory,tenants,memory_storage,clients):
    c=clients[0]
    assert c.post('/api/upload',files={'file':('receipt.png',SAMPLE,'image/png')}).status_code==403
    assert c.post('/api/upload',files={'file':('fake.png',b'<script>bad</script>','image/png')},headers={'X-CSRF-Token':csrf(c)}).status_code==400
    good=c.post('/api/upload',files={'file':('receipt.png',SAMPLE,'image/png')},headers={'X-CSRF-Token':csrf(c)})
    assert good.status_code==200
    assert c.get('/billing').status_code==200
    assert c.get('/categories').status_code==200

def test_quota_month_is_consistent_at_midnight(db_factory,tenants,memory_storage,monkeypatch):
    wid=tenants[1][0].id
    calls=[]
    def rollover():
        calls.append(1)
        return '2026-11' if len(calls)==1 else '2026-12'
    monkeypatch.setattr(services,'month',rollover)
    r=create(db_factory,wid)
    with db_factory() as db:
        assert r.quota_month=='2026-11'
        assert db.get(Usage,(wid,r.quota_month)).used==1
        assert db.get(Usage,(wid,'2026-12')) is None
