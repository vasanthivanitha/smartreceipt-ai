import csv, io, json, secrets, re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from fastapi import FastAPI, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
import stripe
from .config import settings
from .db import get_db
from .models import User, Workspace, Membership, Category, Receipt, Item, Store, Subscription
from . import security, services, storage, billing

app=FastAPI(title='SmartReceipt AI')
app.mount('/static',StaticFiles(directory='app/static'),name='static')
templates=Jinja2Templates(directory='app/templates')

@app.middleware('http')
async def browser_security(request,call_next):
    if request.method in ('POST','PUT','PATCH','DELETE') and request.url.path!='/stripe/webhook':
        expected=request.session.get('csrf','')
        provided=request.headers.get('x-csrf-token','')
        if not provided and 'application/x-www-form-urlencoded' in request.headers.get('content-type',''):
            await request.body()
            provided=(await request.form()).get('csrf','')
        # Multipart forms carry CSRF in the header via fetch to avoid reading unbounded bodies.
        if not expected or not secrets.compare_digest(str(expected).encode(),str(provided).encode()):
            return JSONResponse({'detail':'Invalid CSRF token. Reload this page.'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='SAMEORIGIN'
    response.headers['Cache-Control']='no-store'
    response.headers.setdefault('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' blob:; frame-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self' https://checkout.stripe.com https://billing.stripe.com")
    return response

app.add_middleware(SessionMiddleware,secret_key=settings.secret,session_cookie='sr_session',
    https_only=settings.secure_cookie,same_site='lax',max_age=60*60*12)

from .limits import BodyLimitMiddleware
app.add_middleware(BodyLimitMiddleware,limit=settings.max_bytes+65536)

def context(request,db):
    user=db.get(User,request.session.get('uid',0))
    if not user or request.session.get('version')!=user.session_version: raise HTTPException(401,'Sign in to continue.')
    if not user.verified: raise HTTPException(403,'Verify your email before continuing. Check your inbox or request another link.')
    membership=db.get(Membership,(request.session.get('wid',0),user.id))
    if not membership: raise HTTPException(403,'Workspace access denied.')
    return user,membership

def owner(request,db):
    user,m=context(request,db)
    if m.role!='owner': raise HTTPException(403,'Workspace owner access required.')
    return user,m

def page(request,name,db=None,**values):
    request.session.setdefault('csrf',secrets.token_urlsafe(32))
    values.update(request=request,csrf=request.session['csrf'],max_upload_mb=settings.max_bytes/1048576,max_pdf_pages=settings.max_pages)
    if db and request.session.get('uid'):
        try:
            user,m=context(request,db)
            values.update(user=user,workspace=db.get(Workspace,m.workspace_id),
                memberships=list(db.execute(select(Membership,Workspace).join(Workspace).where(Membership.user_id==user.id))),
                usage=services.usage(db,m.workspace_id))
        except HTTPException: pass
    return templates.TemplateResponse(request=request,name=name,context=values)

@app.exception_handler(HTTPException)
async def http_error(request,exc):
    if request.headers.get('x-requested-with')=='fetch' or request.url.path.startswith('/api/') or request.url.path=='/stripe/webhook':
        return JSONResponse({'detail':exc.detail},status_code=exc.status_code)
    response=page(request,'error.html',message=exc.detail,status=exc.status_code)
    response.status_code=exc.status_code; return response

@app.get('/health')
def health(): return {'status':'ok'}
@app.get('/')
def home(request:Request,db=Depends(get_db)):
    if request.session.get('uid'): return RedirectResponse('/receipts',303)
    return page(request,'auth.html',mode='login')
@app.get('/auth/{mode}')
def auth_page(request:Request,mode:str,token:str='',db=Depends(get_db)):
    if mode not in ('signup','login','forgot','reset','verify','resend'): raise HTTPException(404)
    return page(request,'auth.html',mode=mode,token=token)

def auth_rate(request): security.rate_limit('auth:'+request.client.host,30,900)
def valid_email(email):
    email=email.strip().lower()
    if len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email): raise HTTPException(400,'Enter a valid email address.')
    return email

@app.post('/auth/signup')
def signup(request:Request,email:str=Form(...),password:str=Form(...),name:str=Form('My workspace'),db=Depends(get_db)):
    auth_rate(request); email=valid_email(email)
    user=User(email=email,password=security.hash_password(password))
    try:
        db.add(user); db.flush(); services.create_workspace(db,user,name)
        token=security.issue_token(db,user,'verify'); security.send_link(email,token,'verify'); db.commit()
    except IntegrityError:
        db.rollback()
    return page(request,'message.html',message='If this email is available, a verification link is on its way. Check your inbox, then sign in.')

@app.post('/auth/login')
def login(request:Request,email:str=Form(...),password:str=Form(...),db=Depends(get_db)):
    auth_rate(request); user=db.scalar(select(User).where(User.email==email.strip().lower()))
    # A dummy hash keeps missing-account verification comparable in cost.
    encoded=user.password if user else DUMMY_HASH
    valid=security.check_password(encoded,password)
    if not valid or not user: raise HTTPException(400,'Email or password is incorrect.')
    if not user.verified: raise HTTPException(403,'Verify your email first. Request a new link from the sign-in page if needed.')
    m=db.scalar(select(Membership).where(Membership.user_id==user.id).order_by(Membership.workspace_id))
    request.session.clear(); request.session.update(uid=user.id,wid=m.workspace_id,version=user.session_version,csrf=secrets.token_urlsafe(32))
    return RedirectResponse('/receipts',303)
DUMMY_HASH=security.hasher.hash('not-a-real-account-password')

@app.post('/auth/logout')
def logout(request:Request): request.session.clear(); return RedirectResponse('/',303)
@app.post('/auth/verify')
def verify(request:Request,token:str=Form(...),db=Depends(get_db)):
    auth_rate(request); uid=security.consume_token(db,token,'verify'); db.get(User,uid).verified=True; db.commit()
    return page(request,'message.html',message='Your email is verified. You can now sign in to your private workspace.')
@app.post('/auth/{mode}')
def recovery(request:Request,mode:str,email:str=Form(''),token:str=Form(''),password:str=Form(''),db=Depends(get_db)):
    auth_rate(request)
    if mode=='reset':
        hashed=security.hash_password(password); uid=security.consume_token(db,token,'reset')
        user=db.get(User,uid); user.password=hashed; user.session_version+=1; db.commit(); request.session.clear()
        return page(request,'message.html',message='Password updated. Sign in with your new password.')
    if mode not in ('forgot','resend'): raise HTTPException(404)
    user=db.scalar(select(User).where(User.email==email.strip().lower()))
    if user and (mode=='forgot' or not user.verified):
        purpose='reset' if mode=='forgot' else 'verify'
        token=security.issue_token(db,user,purpose); security.send_link(user.email,token,purpose); db.commit()
    return page(request,'message.html',message='If an eligible account exists, an email is on its way.')

@app.post('/workspace/switch')
def switch(request:Request,workspace_id:int=Form(...),db=Depends(get_db)):
    user,_=context(request,db)
    if not db.get(Membership,(workspace_id,user.id)): raise HTTPException(403,'Workspace access denied.')
    request.session['wid']=workspace_id; return RedirectResponse('/receipts',303)

@app.get('/receipts')
def listing(request:Request,q:str='',state:str='',currency:str='',start:str='',end:str='',db=Depends(get_db)):
    _,m=context(request,db)
    rows=list(db.scalars(services.receipts_query(m.workspace_id,q,state,currency,start,end).limit(250)))
    counts=dict(db.execute(select(Receipt.state,func.count()).where(Receipt.workspace_id==m.workspace_id).group_by(Receipt.state)).all())
    totals=list(db.execute(select(Receipt.currency,func.sum(Receipt.total)).where(Receipt.workspace_id==m.workspace_id,Receipt.state=='confirmed').group_by(Receipt.currency)))
    return page(request,'receipts.html',db,rows=rows,counts=counts,totals=totals,filters=dict(q=q,state=state,currency=currency,start=start,end=end),nav='receipts')

@app.post('/api/upload')
async def upload(request:Request,file:UploadFile=File(...),db=Depends(get_db)):
    _,m=context(request,db); security.rate_limit('upload:'+str(m.workspace_id),60)
    data=await file.read(settings.max_bytes+1)
    try:
        r,duplicate=services.upload(db,m.workspace_id,data); db.commit()
    except Exception:
        db.rollback(); raise
    return {'id':r.id,'duplicate':duplicate,'url':f'/receipts/{r.id}'}

@app.get('/receipts/{rid}')
def review(request:Request,rid:int,db=Depends(get_db)):
    _,m=context(request,db); r=services.owned_receipt(db,m.workspace_id,rid)
    cats=list(db.scalars(select(Category).where(Category.workspace_id==m.workspace_id).order_by(Category.name)))
    if r.state=='confirmed':
        items=list(db.scalars(select(Item).where(Item.workspace_id==m.workspace_id,Item.receipt_id==rid)))
        draft={k:str(getattr(r,k) or '') for k in ('purchase_date','currency','subtotal','tax','discount','total')}
        draft['store']=r.store_guess
        draft['items']=[dict(name=i.name,quantity=str(i.quantity or ''),unit_price=str(i.unit_price) if i.unit_price is not None else '',line_total=str(i.line_total),category_id=i.category_id) for i in items]
    else: draft=json.loads(r.draft)
    return page(request,'review.html',db,receipt=r,draft=draft,flags=json.loads(r.flags),categories=cats,
        stores=list(db.scalars(select(Store).where(Store.workspace_id==m.workspace_id))),nav='receipts')

@app.get('/receipts/{rid}/source')
def source(request:Request,rid:int,download:bool=False,db=Depends(get_db)):
    _,m=context(request,db); r=services.owned_receipt(db,m.workspace_id,rid)
    data=storage.read(r.object_key); ext={'image/jpeg':'jpg','image/png':'png','application/pdf':'pdf'}[r.mime]
    return Response(data,media_type=r.mime,headers={'Content-Disposition':f'{"attachment" if download else "inline"}; filename="receipt-{r.id}.{ext}"','Content-Security-Policy':"sandbox; default-src 'none'"})

@app.post('/api/receipts/{rid}/confirm')
async def confirm(request:Request,rid:int,db=Depends(get_db)):
    _,m=context(request,db)
    try: payload=await request.json()
    except ValueError: raise HTTPException(400,'Invalid JSON')
    if not isinstance(payload,dict): raise HTTPException(400,'Invalid receipt data')
    try: services.confirm(db,m.workspace_id,rid,payload); db.commit()
    except Exception: db.rollback(); raise
    return {'url':f'/receipts/{rid}','message':'Receipt confirmed'}

@app.post('/receipts/{rid}/delete')
def remove(request:Request,rid:int,db=Depends(get_db)):
    _,m=context(request,db); services.delete_receipt(db,m.workspace_id,rid); db.commit()
    return RedirectResponse('/receipts',303)

@app.get('/reports')
def reports(request:Request,currency:str='USD',start:str='',end:str='',db=Depends(get_db)):
    _,m=context(request,db)
    receipts=list(db.scalars(services.receipts_query(m.workspace_id,state='confirmed',currency=currency,start=start,end=end)))
    stores=defaultdict(Decimal); months=defaultdict(Decimal); cats=defaultdict(Decimal)
    tax=Decimal(0); discount=Decimal(0)
    for r in receipts:
        stores[r.store_guess]+=r.total; months[str(r.purchase_date)[:7]]+=r.total; tax+=r.tax; discount+=r.discount
    ids=[r.id for r in receipts]
    if ids:
        for name,total in db.execute(select(Category.name,func.sum(Item.line_total)).join(Category,
            (Item.category_id==Category.id)&(Item.workspace_id==Category.workspace_id)).where(
            Item.workspace_id==m.workspace_id,Item.receipt_id.in_(ids)).group_by(Category.name)):
            cats[name]=total
    currencies=list(db.scalars(select(Receipt.currency).where(Receipt.workspace_id==m.workspace_id,Receipt.state=='confirmed').distinct()))
    return page(request,'reports.html',db,nav='reports',currency=currency,currencies=currencies,start=start,end=end,
        store_totals=sorted(stores.items(),key=lambda x:x[1],reverse=True),month_totals=sorted(months.items()),
        category_totals=sorted(cats.items(),key=lambda x:x[1],reverse=True),total=sum(stores.values(),Decimal(0)),tax=tax,discount=discount,count=len(receipts))

def csv_safe(v):
    text=str(v) if v is not None else ''
    return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text
@app.get('/export.csv')
def export(request:Request,q:str='',state:str='',currency:str='',start:str='',end:str='',kind:str='receipts',db=Depends(get_db)):
    _,m=context(request,db); out=io.StringIO(); writer=csv.writer(out)
    receipts=list(db.scalars(services.receipts_query(m.workspace_id,q,state,currency,start,end)))
    if kind=='items':
        writer.writerow(['receipt_id','store','date','currency','item','category','quantity','unit_price','line_total'])
        by_id={r.id:r for r in receipts}
        if by_id:
            for i,c in db.execute(select(Item,Category.name).join(Category,(Item.category_id==Category.id)&(Item.workspace_id==Category.workspace_id)).where(Item.workspace_id==m.workspace_id,Item.receipt_id.in_(by_id))):
                r=by_id[i.receipt_id]; writer.writerow(map(csv_safe,[r.id,r.store_guess,r.purchase_date,r.currency,i.name,c,i.quantity,i.unit_price,i.line_total]))
    else:
        writer.writerow(['id','store','date','currency','subtotal','tax','discount','grand_total','state'])
        for r in receipts: writer.writerow(map(csv_safe,[r.id,r.store_guess,r.purchase_date,r.currency,r.subtotal,r.tax,r.discount,r.total,r.state]))
    return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="smartreceipt.csv"'})

@app.get('/categories')
def categories(request:Request,db=Depends(get_db)):
    _,m=owner(request,db)
    return page(request,'categories.html',db,nav='categories',categories=list(db.scalars(select(Category).where(Category.workspace_id==m.workspace_id))))
@app.post('/categories')
def category_edit(request:Request,name:str=Form(...),category_id:int=Form(0),db=Depends(get_db)):
    _,m=owner(request,db); services.lock_workspace(db,m.workspace_id); name=name.strip()[:100]
    if not name: raise HTTPException(400,'Category name is required.')
    if category_id:
        c=db.scalar(select(Category).where(Category.id==category_id,Category.workspace_id==m.workspace_id))
        if not c: raise HTTPException(404,'Category not found')
        c.name=name
    else: db.add(Category(workspace_id=m.workspace_id,name=name))
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409,'A category with this name already exists.')
    return RedirectResponse('/categories',303)

@app.get('/billing')
def billing_page(request:Request,db=Depends(get_db)):
    _,m=owner(request,db)
    return page(request,'billing.html',db,nav='billing',subscription=db.get(Subscription,m.workspace_id),free=settings.free_limit,paid=settings.paid_limit,configured=bool(settings.stripe_key and settings.stripe_price))
@app.post('/billing/checkout')
def checkout(request:Request,db=Depends(get_db)):
    user,m=owner(request,db); return RedirectResponse(billing.checkout(db,m.workspace_id,user.email),303)
@app.post('/billing/portal')
def portal(request:Request,db=Depends(get_db)):
    _,m=owner(request,db); return RedirectResponse(billing.portal(db,m.workspace_id),303)
@app.post('/stripe/webhook')
async def webhook(request:Request,db=Depends(get_db)):
    if not settings.stripe_webhook or not settings.stripe_key: raise HTTPException(503,'Billing is not configured.')
    body=await request.body()
    if len(body)>1024*1024: raise HTTPException(413)
    stripe.api_key=settings.stripe_key
    try: event=stripe.Webhook.construct_event(body,request.headers.get('stripe-signature',''),settings.stripe_webhook)
    except (ValueError,stripe.SignatureVerificationError): raise HTTPException(400,'Invalid webhook signature.')
    try: billing.apply_event(db,event); db.commit()
    except Exception: db.rollback(); raise HTTPException(503,'Billing sync unavailable; retry this event.') from None
    return {'received':True}
