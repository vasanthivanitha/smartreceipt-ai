import hashlib, json, time, hmac
from datetime import datetime,timedelta
from sqlalchemy import select,func
from app.models import User,Membership,MailToken,Subscription,StripeEvent,Usage
from app import security,billing
from app.main import app
from app.config import settings
from fastapi.testclient import TestClient
from .conftest import csrf

def test_full_auth_verification_reset(db_factory,monkeypatch):
    monkeypatch.setattr(security,'rate_limit',lambda *a:None)
    sent=[]; monkeypatch.setattr(security,'send_link',lambda email,token,purpose:sent.append((token,purpose)))
    with TestClient(app) as c:
        def token(path='/'): return c.get(path).text.split('name="csrf-token" content="')[1].split('"')[0]
        response=c.post('/auth/signup',data=dict(csrf=token(),email='new@example.test',password='a-long-test-password',name='Private'))
        assert response.status_code==200 and sent[0][1]=='verify'
        assert c.post('/auth/login',data=dict(csrf=token(),email='new@example.test',password='a-long-test-password')).status_code==403
        verification=sent[0][0]
        assert c.post('/auth/verify',data=dict(csrf=token(),token=verification)).status_code==200
        assert c.post('/auth/verify',data=dict(csrf=token(),token=verification)).status_code==400
        assert c.post('/auth/login',data=dict(csrf=token(),email='new@example.test',password='a-long-test-password'),follow_redirects=False).status_code==303
        assert c.get('/receipts').status_code==200
        old_cookie=c.cookies.get('sr_session')
        assert c.post('/auth/forgot',data=dict(csrf=csrf(c),email='new@example.test')).status_code==200
        reset=sent[-1][0]
        assert c.post('/auth/reset',data=dict(csrf=token('/auth/reset?token='+reset),token=reset,password='another-long-password')).status_code==200
        c.cookies.set('sr_session',old_cookie)
        assert c.get('/receipts').status_code==401
    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Membership))==1
        assert db.scalar(select(func.count()).select_from(MailToken))==0

def event(eid='evt_synthetic'):
    return {'id':eid,'type':'customer.subscription.updated','data':{'object':{'id':'sub_synthetic','customer':'cus_synthetic'}}}

def test_billing_verified_state_and_replay(db_factory,tenants,monkeypatch):
    wid=tenants[1][0].id
    monkeypatch.setattr(settings,'stripe_price','price_synthetic')
    state={'status':'active','items':{'data':[{'price':{'id':'price_synthetic'}}]},'cancel_at_period_end':False}
    monkeypatch.setattr(billing.stripe.Subscription,'retrieve',lambda sid:state)
    with db_factory() as db:
        db.get(Subscription,wid).customer_id='cus_synthetic'; db.commit()
        billing.apply_event(db,event()); db.commit()
        assert db.get(Subscription,wid).paid
        from app.services import usage
        assert usage(db,wid)[1]==settings.paid_limit
        billing.apply_event(db,event()); db.commit()
        assert db.scalar(select(func.count()).select_from(StripeEvent))==1
        state['status']='past_due'
        billing.apply_event(db,event('evt_payment_failed')); db.commit()
        assert not db.get(Subscription,wid).paid
        state['status']='active'; state['cancel_at_period_end']=True
        billing.apply_event(db,event('evt_cancel_scheduled')); db.commit()
        assert db.get(Subscription,wid).paid and db.get(Subscription,wid).cancel_at_period_end
        state['status']='canceled'
        billing.apply_event(db,event('evt_canceled')); db.commit()
        assert not db.get(Subscription,wid).paid

def test_webhook_signature_required_and_valid(db_factory,tenants,monkeypatch):
    monkeypatch.setattr(settings,'stripe_key','sk_test_synthetic_not_a_real_key')
    monkeypatch.setattr(settings,'stripe_webhook','whsec_synthetic_not_a_real_secret')
    calls=[]; monkeypatch.setattr(billing,'apply_event',lambda db,event:calls.append(event['id']))
    body=json.dumps(event()).encode(); timestamp=int(time.time())
    sig=hmac.new(settings.stripe_webhook.encode(),str(timestamp).encode()+b'.'+body,hashlib.sha256).hexdigest()
    with TestClient(app) as c:
        assert c.post('/stripe/webhook',content=body,headers={'stripe-signature':'invalid'}).status_code==400
        assert c.post('/stripe/webhook',content=body,headers={'stripe-signature':f't={timestamp},v1={sig}'}).status_code==200
    assert calls==['evt_synthetic']
