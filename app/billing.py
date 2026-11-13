import stripe
from fastapi import HTTPException
from sqlalchemy import select
from .config import settings
from .models import Subscription, StripeEvent
from .services import lock_workspace

def ready():
    if not settings.stripe_key or not settings.stripe_price: raise HTTPException(503,'Stripe test-mode billing is not configured.')
    stripe.api_key=settings.stripe_key

def checkout(db,wid,email):
    ready(); lock_workspace(db,wid)
    sub=db.get(Subscription,wid)
    if sub.paid: raise HTTPException(409,'Use the billing portal to manage your subscription.')
    if not sub.customer_id:
        customer=stripe.Customer.create(email=email,metadata={'workspace_id':str(wid)},idempotency_key=f'workspace-{wid}')
        sub.customer_id=customer.id; db.commit()
    session=stripe.checkout.Session.create(customer=sub.customer_id,mode='subscription',
        line_items=[{'price':settings.stripe_price,'quantity':1}],
        success_url=settings.base_url+'/billing?checkout=success',cancel_url=settings.base_url+'/billing',
        subscription_data={'metadata':{'workspace_id':str(wid)}})
    return session.url

def portal(db,wid):
    ready(); sub=db.get(Subscription,wid)
    if not sub.customer_id: raise HTTPException(400,'No billing customer exists yet.')
    return stripe.billing_portal.Session.create(customer=sub.customer_id,return_url=settings.base_url+'/billing').url

def apply_event(db,event):
    # Serialize per workspace, then retrieve CURRENT Stripe state under the lock.
    # Arrival order of webhooks cannot regress the local subscription state.
    kind=event['type']; obj=event['data']['object']
    if kind.startswith('customer.subscription.'):
        customer=obj.get('customer'); sid=obj['id']
    elif kind in ('invoice.paid','invoice.payment_failed','checkout.session.completed'):
        customer=obj.get('customer'); sid=obj.get('subscription')
        if not sid and kind.startswith('invoice.'):
            sid=(obj.get('parent') or {}).get('subscription_details',{}).get('subscription')
    else: return
    if not customer or not sid: return
    sub=db.scalar(select(Subscription).where(Subscription.customer_id==customer))
    if not sub: return
    lock_workspace(db,sub.workspace_id)
    if db.get(StripeEvent,event['id']): return
    current=stripe.Subscription.retrieve(sid)
    # Ignore an obsolete subscription once another subscription is linked.
    if sub.subscription_id and sub.subscription_id!=sid:
        old=stripe.Subscription.retrieve(sub.subscription_id)
        if old['status'] not in ('canceled','incomplete_expired'): return
    prices=[i['price']['id'] for i in current['items']['data']]
    sub.subscription_id=sid; sub.status=current['status']
    sub.paid=current['status'] in ('active','trialing') and settings.stripe_price in prices
    sub.cancel_at_period_end=bool(current.get('cancel_at_period_end',False))
    db.add(StripeEvent(id=event['id'])); db.flush()
