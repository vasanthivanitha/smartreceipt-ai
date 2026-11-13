import os
from dataclasses import dataclass

@dataclass
class Settings:
    database_url: str = os.getenv('DATABASE_URL', 'mysql+pymysql://receipt:receipt@mysql/receipt')
    redis_url: str = os.getenv('REDIS_URL', 'redis://redis:6379/0')
    secret: str = os.getenv('SESSION_SECRET', 'development-only-change-this-secret')
    secure_cookie: bool = os.getenv('SECURE_COOKIE', 'true').lower() == 'true'
    base_url: str = os.getenv('BASE_URL', 'http://localhost:8000')
    s3_endpoint: str = os.getenv('S3_ENDPOINT', 'http://minio:9000')
    s3_bucket: str = os.getenv('S3_BUCKET', 'receipts')
    s3_key: str = os.getenv('S3_ACCESS_KEY', 'minioadmin')
    s3_secret: str = os.getenv('S3_SECRET_KEY', 'minioadmin')
    free_limit: int = int(os.getenv('FREE_LIMIT', '20'))
    paid_limit: int = int(os.getenv('PAID_LIMIT', '500'))
    max_bytes: int = int(os.getenv('MAX_UPLOAD_BYTES', '10485760'))
    max_pages: int = int(os.getenv('MAX_PDF_PAGES', '10'))
    stripe_key: str = os.getenv('STRIPE_SECRET_KEY', '')
    stripe_webhook: str = os.getenv('STRIPE_WEBHOOK_SECRET', '')
    stripe_price: str = os.getenv('STRIPE_PRICE_ID', '')
    smtp_host: str = os.getenv('SMTP_HOST', 'mailpit')
    smtp_port: int = int(os.getenv('SMTP_PORT', '1025'))
    smtp_user: str = os.getenv('SMTP_USER', '')
    smtp_password: str = os.getenv('SMTP_PASSWORD', '')
    smtp_tls: bool = os.getenv('SMTP_TLS', 'false').lower() == 'true'
    mail_from: str = os.getenv('MAIL_FROM', 'hello@smartreceipt.local')

settings = Settings()

if settings.secure_cookie and (len(settings.secret)<32 or settings.secret in {
    'development-only-change-this-secret',
    'local-development-replace-with-a-random-64-character-secret',
}):
    raise RuntimeError('Set a strong SESSION_SECRET before enabling secure sessions.')
