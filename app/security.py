import hashlib, secrets, smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from redis import Redis
from fastapi import HTTPException
from sqlalchemy import select, delete
from .config import settings
from .models import MailToken

hasher = PasswordHasher()
def hash_password(password):
    if len(password) < 12 or len(password) > 256: raise HTTPException(400, 'Use a password of 12–256 characters.')
    return hasher.hash(password)
def check_password(encoded, password):
    try: return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError): return False

def rate_limit(key, maximum, seconds=3600):
    # Atomic INCR + expiry; fail closed when Redis is unavailable.
    redis = Redis.from_url(settings.redis_url)
    count = redis.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n", 1, 'rate:'+key, seconds)
    if count > maximum: raise HTTPException(429, 'Too many requests. Try again later.')

def issue_token(db, user, purpose):
    db.execute(delete(MailToken).where(MailToken.user_id==user.id, MailToken.purpose==purpose))
    token = secrets.token_urlsafe(32)
    db.add(MailToken(digest=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
        purpose=purpose, expires=datetime.utcnow()+timedelta(hours=1)))
    return token

def consume_token(db, token, purpose):
    row = db.scalar(select(MailToken).where(MailToken.digest==hashlib.sha256(token.encode()).hexdigest(),
        MailToken.purpose==purpose, MailToken.expires>datetime.utcnow()).with_for_update())
    if not row: raise HTTPException(400, 'This link has expired or has already been used.')
    uid=row.user_id
    db.delete(row)
    return uid

def send_link(email, token, purpose):
    msg=EmailMessage()
    msg['From']=settings.mail_from; msg['To']=email
    msg['Subject']='Verify your SmartReceipt account' if purpose=='verify' else 'Reset your SmartReceipt password'
    msg.set_content(f"Open this link within one hour:\n{settings.base_url}/auth/{purpose}?token={token}\n")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_tls: smtp.starttls()
            if settings.smtp_user: smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except (OSError,smtplib.SMTPException):
        raise HTTPException(503,'Email delivery is unavailable. Please try again later.') from None
