import hashlib, json, re, uuid
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, delete
from fastapi import HTTPException
from .models import Workspace, Membership, Category, Store, Receipt, Item, Subscription, Usage, Deletion
from .config import settings
from . import storage
from .ocr import CATEGORIES, validate

ZERO=Decimal('0.00')
def month(): return datetime.utcnow().strftime('%Y-%m')
def lock_workspace(db,wid):
    w=db.scalar(select(Workspace).where(Workspace.id==wid).with_for_update())
    if not w: raise HTTPException(404,'Workspace not found')
    return w

def create_workspace(db,user,name):
    w=Workspace(name=name[:100] or 'My workspace'); db.add(w); db.flush()
    db.add(Membership(workspace_id=w.id,user_id=user.id,role='owner'))
    db.add(Subscription(workspace_id=w.id))
    db.add_all(Category(workspace_id=w.id,name=n) for n in CATEGORIES)
    db.flush(); return w

def owned_receipt(db,wid,rid,lock=False):
    q=select(Receipt).where(Receipt.id==rid,Receipt.workspace_id==wid)
    if lock: q=q.with_for_update()
    r=db.scalar(q)
    if not r: raise HTTPException(404,'Receipt not found')
    return r

def usage(db,wid,period=None):
    period=period or month()
    sub=db.get(Subscription,wid)
    u=db.get(Usage,(wid,period))
    return (u.used if u else 0), (settings.paid_limit if sub and sub.paid else settings.free_limit)

def upload(db,wid,data):
    mime=validate(data); digest=hashlib.sha256(data).hexdigest()
    # Commit staging cleanup BEFORE taking the workspace lock: its foreign key
    # must not compete with that lock. Any interrupted write is eventually removed.
    key=f'{wid}/{uuid.uuid4().hex}'
    db.add(Deletion(workspace_id=wid,object_key=key,next_attempt=datetime.utcnow()+timedelta(days=1)))
    db.commit()
    # Workspace lock serializes reservations, duplicate checks and plan changes.
    lock_workspace(db,wid)
    existing=db.scalar(select(Receipt).where(Receipt.workspace_id==wid,Receipt.sha256==digest))
    if existing:
        db.execute(delete(Deletion).where(Deletion.object_key==key,Deletion.workspace_id==wid))
        return existing,True
    period=month()
    used,limit=usage(db,wid,period)
    if used>=limit: raise HTTPException(403,'Your monthly upload limit has been reached.')
    storage.put(key,data,mime)
    u=db.get(Usage,(wid,period))
    if not u: u=Usage(workspace_id=wid,month=period,used=0); db.add(u)
    u.used+=1
    r=Receipt(workspace_id=wid,sha256=digest,object_key=key,mime=mime,quota_month=period)
    db.add(r); db.flush()
    db.execute(delete(Deletion).where(Deletion.workspace_id==wid,Deletion.object_key==key))
    return r,False

def release(db,r):
    if r.quota_reserved:
        u=db.get(Usage,(r.workspace_id,r.quota_month))
        if u: u.used=max(0,u.used-1)
        r.quota_reserved=False

def money(value,optional=False):
    if optional and value in ('',None): return None
    try:
        d=Decimal(str(value))
        if not d.is_finite() or d<0 or d>Decimal('999999999.99') or d!=d.quantize(Decimal('.01')): raise ValueError()
        return d
    except (InvalidOperation,ValueError,TypeError): raise HTTPException(400,'Amounts must be non-negative numbers with at most two decimals.')

def confirm(db,wid,rid,payload):
    lock_workspace(db,wid); r=owned_receipt(db,wid,rid,True)
    if r.state not in ('needs_review','confirmed'): raise HTTPException(409,'This receipt is not ready for review.')
    name=str(payload.get('store','')).strip()[:150]
    if not name: raise HTTPException(400,'Store is required.')
    normalized=re.sub(r'\W+',' ',name.casefold()).strip()
    if not normalized: raise HTTPException(400,'Invalid store name.')
    store=db.scalar(select(Store).where(Store.workspace_id==wid,Store.normalized==normalized))
    if not store: store=Store(workspace_id=wid,name=name,normalized=normalized); db.add(store); db.flush()
    try: purchase=date.fromisoformat(payload['purchase_date'])
    except (KeyError,ValueError,TypeError): raise HTTPException(400,'A valid purchase date is required.')
    currency=str(payload.get('currency','')).upper()
    if not re.fullmatch('[A-Z]{3}',currency): raise HTTPException(400,'Use a three-letter currency code.')
    amounts={k:money(payload.get(k)) for k in ['subtotal','tax','discount','total']}
    rows=payload.get('items',[])
    if not isinstance(rows,list) or not 1<=len(rows)<=500: raise HTTPException(400,'Add between 1 and 500 items.')
    categories={c.id:c for c in db.scalars(select(Category).where(Category.workspace_id==wid))}
    items=[]
    for row in rows:
        if not isinstance(row,dict): raise HTTPException(400,'Invalid item row.')
        try: cid=int(row.get('category_id',0))
        except (ValueError,TypeError): raise HTTPException(400,'Invalid category.')
        if cid not in categories: raise HTTPException(400,'Category does not belong to your workspace.')
        item_name=str(row.get('name','')).strip()[:200]
        if not item_name: raise HTTPException(400,'Item names are required.')
        try:
            qty=None if row.get('quantity') in (None,'') else Decimal(str(row['quantity']))
            if qty is not None and (not qty.is_finite() or qty<=0 or qty>999999 or qty!=qty.quantize(Decimal('.001'))): raise ValueError()
        except (InvalidOperation,ValueError,TypeError): raise HTTPException(400,'Invalid quantity (up to three decimals).')
        unit=money(row.get('unit_price'),True); line=money(row.get('line_total'))
        if qty is not None and unit is not None and abs(qty*unit-line)>Decimal('.01') and payload.get('accept_discrepancy') is not True:
            raise HTTPException(409,'Quantity × unit price differs from line total. Correct it or acknowledge discrepancies.')
        items.append(Item(workspace_id=wid,receipt_id=rid,category_id=cid,name=item_name,quantity=qty,unit_price=unit,line_total=line))
    discrepancy=amounts['subtotal']+amounts['tax']-amounts['discount']-amounts['total']
    item_diff=sum((i.line_total for i in items),ZERO)-amounts['subtotal']
    if (abs(discrepancy)>Decimal('.01') or abs(item_diff)>Decimal('.01')) and payload.get('accept_discrepancy') is not True:
        raise HTTPException(409,'Totals do not reconcile. Correct the amounts or acknowledge discrepancies.')
    possible=db.scalar(select(Receipt.id).where(Receipt.workspace_id==wid,Receipt.id!=rid,
        Receipt.state=='confirmed',Receipt.store_id==store.id,Receipt.purchase_date==purchase,
        Receipt.currency==currency,Receipt.total==amounts['total']))
    if possible and payload.get('distinct_purchase') is not True:
        raise HTTPException(409,'Possible duplicate purchase: same store, date, currency and total. Confirm this is a distinct purchase.')
    db.execute(delete(Item).where(Item.workspace_id==wid,Item.receipt_id==rid))
    db.add_all(items)
    r.store_id=store.id; r.store_guess=store.name; r.purchase_date=purchase; r.currency=currency
    for k,v in amounts.items(): setattr(r,k,v)
    r.flags=json.dumps(['Customer acknowledged discrepancies.'] if abs(discrepancy)>Decimal('.01') or abs(item_diff)>Decimal('.01') else [])
    r.state='confirmed'; r.updated_at=datetime.utcnow()
    r.draft='{}'
    db.flush(); return r

def delete_receipt(db,wid,rid):
    lock_workspace(db,wid); r=owned_receipt(db,wid,rid,True)
    db.add(Deletion(workspace_id=wid,object_key=r.object_key))
    if r.state in ('queued','processing'): release(db,r)
    # Accepted successful receipts continue to count toward monthly usage after deletion.
    db.execute(delete(Item).where(Item.workspace_id==wid,Item.receipt_id==rid))
    db.delete(r)

def receipts_query(wid,q='',state='',currency='',start='',end='',store=''):
    stmt=select(Receipt).where(Receipt.workspace_id==wid)
    if q: stmt=stmt.where(Receipt.store_guess.contains(q,autoescape=True))
    if state: stmt=stmt.where(Receipt.state==state)
    if currency: stmt=stmt.where(Receipt.currency==currency.upper())
    if store: stmt=stmt.where(Receipt.store_id==int(store))
    try:
        if start: stmt=stmt.where(Receipt.purchase_date>=date.fromisoformat(start))
        if end: stmt=stmt.where(Receipt.purchase_date<=date.fromisoformat(end))
    except ValueError: raise HTTPException(400,'Invalid filter date.')
    return stmt.order_by(Receipt.created_at.desc())
