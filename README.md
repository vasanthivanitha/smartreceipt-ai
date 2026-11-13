# SmartReceipt AI

A runnable receipt-management MVP using Python 3.12, FastAPI, Jinja2, Bootstrap 5, MySQL 8.4, SQLAlchemy/Alembic, Redis/RQ, Tesseract/Pillow, private S3-compatible MinIO storage, and Stripe Checkout/Customer Portal.

## Local startup

Requires Docker Engine with Compose v2, internet access for the first build, and approximately **12 GB free disk** (MinIO is built from pinned source). Nothing is published externally. Web, Mailpit and MinIO console ports bind to localhost; the database, Redis and S3 API are private to the Compose network.

```sh
git clone https://github.com/vasanthivanitha/smartreceipt-ai.git
cd smartreceipt-ai
cp .env.example .env
docker compose up --build -d
docker compose ps
docker compose logs --tail=50 web worker maintenance migrate storage-init
```

Open **http://localhost:8000** and create an account. Open **http://localhost:8025** (Mailpit), follow the verification email, click Verify, and sign in. A private workspace, one owner membership, a free subscription and five categories are created for each new account. No invitation UI is included.

Upload `samples/synthetic-receipt.png`, `samples/synthetic-text.pdf`, or `samples/synthetic-scanned.pdf`. Maintenance dispatches queued receipts every 30 seconds; an RQ worker extracts them. Review the original next to the form, correct the milk line if desired, and save Milk → Groceries (4.50), Paper towels → Household (6.00), Shampoo → Personal Care (8.00). Subtotal is 18.50, tax 1.50, discount 0.00 and grand total USD 20.00. Reports then show USD 20.00 by store/month and three separate category totals. Each fixture is synthetic; PDF and PNG fixtures represent the same purchase and exercise the possible-duplicate warning when both are confirmed.

Optional sample account (development only; explicitly bypasses email verification):

```sh
docker compose exec web python -m app.seed --email demo@example.test --password synthetic-demo-password
```

The seeder uploads and extracts one synthetic receipt into a new workspace; review it after signing in. It refuses an existing account. Generate fixtures again with `docker compose exec web python samples/generate.py`.

MinIO console: **http://localhost:9001**, using the development S3 credentials in `.env`. Do not share these development credentials or expose the console in production. The bucket is created with **no public policy**. The application serves authenticated downloads after checking the current membership; object keys and public URLs are never sent to the browser.

Stop with `docker compose down`. Named volumes preserve data. `docker compose down -v` **irreversibly removes all local receipts and databases**; only use that to intentionally reset development data.

### MinIO distribution

The former pinned public MinIO image and `mc` image were unavailable during verification (registry authorization denial). Compose therefore builds the unmodified MinIO `RELEASE.2025-04-22T22-12-26Z` source from GitHub, verifies its SHA-256, and uses the Python S3 client to initialize the private bucket. The first build is heavier than pulling a binary image. MinIO is AGPLv3: its source and license are available at https://github.com/minio/minio/tree/RELEASE.2025-04-22T22-12-26Z. Review its licensing before distributing MinIO. See `docker/minio.Dockerfile`. Bootstrap CSS retains its MIT license header.

## Tests

Fast isolated tests use SQLite and in-memory object storage, with real OCR. Locking tests require **a disposable MySQL database**; SQLite cannot validate `FOR UPDATE`. Tests **drop and recreate all tables in TEST_DATABASE_URL**. Never point it at application data.

Inside the app image (Tesseract is installed):

```sh
docker compose exec web pytest -q -W ignore::DeprecationWarning
```

Create a disposable database with the development root password configured in `.env`:

```sh
docker compose exec mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "CREATE DATABASE IF NOT EXISTS receipt_test; GRANT ALL ON receipt_test.* TO '\''receipt'\''@'\''%'\'';"'
docker compose exec -e TEST_DATABASE_URL=mysql+pymysql://receipt:receipt@mysql/receipt_test web pytest -q -W ignore::DeprecationWarning
```

Host development alternative (install Tesseract using your OS package manager):

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pytest -q -W ignore::DeprecationWarning
```

Tests cover two-customer file/record/export isolation, database-enforced tenant relationships, review and atomic confirmation, corrected item categories, currency separation, file hashes, possible duplicate purchases, actual PNG and text/scanned-PDF extraction, password verification/reset and old-session invalidation, CSRF, deletion retries, job fencing and permanent failure release. MySQL-only tests race four uploads for one remaining slot and race four identical uploads. Stripe tests verify HMAC signatures and exercise upgrade, replay, cancellation and payment failure using synthetic provider responses.

Migrations run before web/worker/maintenance startup. Check the deployed schema with:

```sh
docker compose exec web alembic current
docker compose exec web alembic check
```

Optional external-service smoke test (creates a synthetic account and deletes its test receipt):

```sh
docker compose exec web python -m tests.integration_smoke
```

This uses actual SMTP/Mailpit, Redis/RQ, object storage and Tesseract; it is separate from the isolated test suite.

## Stripe test-mode setup

No real or test account credentials are bundled. In Stripe **test mode**, create a Product and a recurring **monthly Price**, enable the Customer Portal, and place the following in `.env`:

```dotenv
STRIPE_SECRET_KEY=sk_test_your_own_key
STRIPE_PRICE_ID=price_your_monthly_test_price
STRIPE_WEBHOOK_SECRET=whsec_your_listener_secret
```

Run the Stripe CLI on the host:

```sh
stripe login
stripe listen --forward-to localhost:8000/stripe/webhook
```

Use the `whsec_...` printed by that listener (never paste it into chat). Restart app processes after changing configuration:

```sh
docker compose up -d --force-recreate web worker maintenance
```

Upgrade from Plan & billing. Use Stripe's test card **4242 4242 4242 4242**, a future expiry and any three-digit CVC. The return from Checkout does **not** grant Plus. The verified webhook retrieves the **current subscription** from Stripe and only enables the configured Price for `active`/`trialing` status. The upload limit changes from 20 to 500 after that transaction commits. Refresh billing to see the result. Customer Portal handles cancellation and payment-method changes. Period-end cancellation retains access until the subscription stops being active; `past_due`, `unpaid`, canceled, or expired subscriptions use Free. Existing receipts remain accessible when a plan is downgraded; new uploads are blocked if current usage exceeds the new limit.

Configure production webhook delivery for:

- `checkout.session.completed`
- `customer.subscription.created`, `.updated`, `.deleted`
- `invoice.paid`, `invoice.payment_failed`

Signatures are verified using the raw body with the Stripe SDK's timestamp tolerance. Events are deduplicated by event ID in the same transaction as subscription changes. Plan updates serialize on the workspace and fetch current provider state rather than trusting event order. Temporary provider/database failures return 503 so Stripe retries. A webhook cannot create or choose an arbitrary workspace; it must map to a customer previously created by authenticated, owner-only Checkout.

## Processing, consistency, and deletion

- Quotas use calendar months in **UTC**, configurable with `FREE_LIMIT` and `PAID_LIMIT`. Counts include queued, processing, needs-review and confirmed receipts. A locked workspace row serializes reservation and subscription changes. Hash duplicate checks and usage increments occur in the receipt transaction. SHA-256 uniqueness is per workspace, so identical files in different workspaces remain independent.
- A staged deletion record is committed before creating an object. The successful receipt transaction removes that record. An interrupted/failed upload therefore leaves a durable cleanup intent for any orphan; it is eligible after 24 hours. Reservation and receipt insert roll back together if object upload or persistence fails.
- The receipt table acts as a durable processing outbox: Redis downtime cannot lose an accepted receipt. Maintenance re-dispatches queued jobs and jobs whose 10-minute lease expired. Claims, attempts and completion updates are workspace-scoped. A random claim token fences stale workers. Queue dispatch duplication is safe. OCR has bounded execution time; jobs have a 9-minute timeout. Three failed/abandoned attempts permanently fail the receipt and release its original month's reservation once.
- Extraction writes a JSON draft only. Confirmation validates all fields/categories, checks arithmetic and possible duplicates, deletes old item rows, inserts the complete new item set, and updates the confirmed receipt **in one transaction**. Reconfirming replaces items rather than appending duplicates. Draft text is never exported or used in reports. Raw OCR text is not retained.
- Missing currency is flagged, never silently inferred from an ambiguous `$`. ISO-like currency codes and monetary values must be supplied at confirmation. Quantities and unit prices can remain blank if not visible. Amounts use database `DECIMAL`; review rejects invalid precision, negative values and overflow. Returns/credit receipts are outside this MVP.
- Store names normalize case and punctuation. The review form offers saved stores; choosing the corrected saved name links to that store. Item category suggestions use conservative keyword rules and remain editable. Arithmetic discrepancies and same-store/date/currency/total duplicates require explicit customer acknowledgment.
- Store/month reports use **confirmed grand totals**. Category reports use **confirmed item line totals**, with receipt-level tax and discounts displayed separately and no proportional allocation. Select one currency at a time; there is no exchange-rate conversion.
- Deleting a receipt transactionally removes items/receipt and inserts a deletion-outbox row. Access disappears immediately. The maintenance process deletes its object; failures retry exponentially (60 seconds initially, capped at 24 hours) until successful. Monitor this outbox; it deliberately has no automatic permanent abandonment. Deleting queued/processing receipts releases their reservation. Deleting reviewed/confirmed receipts does not replenish usage, preventing unlimited processing by deletion. Deleted hashes can be uploaded again as a new upload.
- CSV receipt exports include all filtered receipts; item exports contain saved item rows only. Text cells are protected against spreadsheet formula injection. The library lists up to 250 matching receipts; exports include all matches. Large-data pagination/streaming is a future enhancement.

## Production configuration

Before deploying to a Python/container host:

1. Set a cryptographically random `SESSION_SECRET` (e.g. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`), `SECURE_COOKIE=true`, and an HTTPS `BASE_URL`. The built-in development secret is rejected with secure sessions enabled. Rotate it to invalidate all sessions. Password resets also invalidate a user's old sessions.
2. Use distinct strong MySQL/S3 credentials, TLS connections, backups and restore drills. Point `DATABASE_URL`, `REDIS_URL`, and S3 settings to private infrastructure; restrict all service network access. Do not expose MySQL, Redis, the object API, Mailpit or admin consoles publicly. Use an encrypted S3 bucket and a least-privilege application identity. This MVP uses S3-compatible authenticated downloads rather than public files.
3. Replace Mailpit with your SMTP provider; set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_TLS=true`, and a verified `MAIL_FROM`. No email bodies, tokens, passwords or receipt data should be logged. The supplied Uvicorn command disables access logs (verification/reset URLs contain tokens). Keep query strings out of reverse-proxy logs too. SQLAlchemy hides bound parameters; queue failures emit only sanitized OCR errors.
4. Configure reverse-proxy TLS, allowed hostnames, request timeouts, a matching body-size limit, and the exact trusted proxy chain. Do not trust arbitrary forwarded IP headers. Redis-backed fixed-window authentication and upload limits fail closed when Redis is down. Isolate Redis from other applications. CSRF is required for every browser mutation, including uploads; the signed HttpOnly session cookie is SameSite=Lax.
5. Run migrations once per release, then web, workers and maintenance. One maintenance instance is sufficient; duplicates are safe. Monitor queue depth, long-running jobs, failed receipts, SMTP, MySQL, Redis and `file_deletions`. Configure external alerts, resource limits and backups. OCR and PDF parsing handle untrusted files: keep libraries patched and run workers in restricted containers with CPU/memory limits. Validation checks actual file format, decoded image dimensions, byte count, encryption and page count; an ASGI guard bounds actual bytes before multipart parsing.
6. Configure Stripe keys/webhooks for the target environment, Customer Portal and the intended recurring Price. Perform the real test-mode upgrade/cancel/payment-failure acceptance flow before accepting customers. The synthetic test credentials in automated tests cannot call Stripe. Put secrets in a secrets manager, not Git.

The parser is a heuristic OCR-assisted MVP, not an LLM service. Complex layouts, non-English receipts, comma thousands separators, receipt-wide rebates and unusual date formats may require manual correction. There is no invitation UI, workspace administration UI, automatic tax allocation, currency conversion or malware-scanning integration in this release. Membership and tenant database relationships are implemented now for future invitations. Email delivery failure rolls back account/token creation and requires retry after SMTP recovers.

## Verification record

See `VERIFICATION.md` for what was actually run in the delivery environment and outstanding external checks. This project is delivered as source for the requested stack; it is not deployed to Sites, whose Worker runtime does not support Python/MySQL/RQ processes.
