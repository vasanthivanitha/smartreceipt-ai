# Verification and handoff

## Confirmed

- Python 3.12 dependencies installed from pinned requirements.
- Final application Docker image built successfully. Inside-image suite: **21 passed, 2 MySQL-only tests skipped**. The real-service smoke flow also passed from inside that image.
- Isolated suite: **21 passed, 2 MySQL-only tests skipped**. Uses SQLite for fast tests, mocked object storage/provider responses, and actual Tesseract for image/scanned PDF extraction.
- MySQL 8.4 suite: **20 passed**, including both concurrency tests. This run preceded the final single-month capture, strict acknowledgment validation, SMTP error-message and small UI refinements; those were subsequently covered by the isolated suite (the added month-rollover and PDF validation tests explain its higher count).
- Four concurrent distinct uploads for one available slot accept exactly one receipt and reserve one slot.
- Four concurrent identical uploads create one receipt, reserve one slot and return duplicate handling to the other callers.
- Two-workspace read/download/write/export isolation; composite database foreign keys reject cross-workspace category linkage.
- Corrected milk/paper towels/shampoo receipt confirms exactly three item rows. Reconfirmation/job replay does not append duplicates. Store/category/month and currency-separated reports and CSV return the expected amounts.
- Actual Tesseract PNG/scanned PDF extraction and embedded PDF text extraction identify the synthetic receipt's three items and USD 20.00 total.
- Signup/verification/password reset, one-use tokens and old-session invalidation tested through HTTP using a mocked mail transport.
- Signed Stripe payload accepted; invalid signature rejected. Provider-response tests cover active upgrade, replay, period-end cancellation, cancellation and payment failure. No real provider credentials used.
- Durable deletion retry, wrong-workspace processing job, stale worker fencing, permanent-failure quota release, and CSRF tested.
- Initial Alembic migration applied to real MySQL 8.4; `alembic check` returned “No new upgrade operations detected.” MySQL-generated foreign-key indexes are explicitly represented in the source.
- Docker Compose was repaired before GitHub delivery: the earlier package had an incomplete service declaration that an empty-model config check did not catch. The repaired config is validated by asserting all nine expected services, their startup dependencies and the Python worker command. Python compile checks pass.

## Verification limits

- An initial application-image build exhausted this environment's 7.9 GB filesystem while creating Docker VFS layers. After removing task scratch/build cache and stopping the test MySQL container, the application image built successfully and passed its tests. The complete Compose deployment, including the multi-stage MinIO source image, was not run end to end. The README recommends at least 12 GB free disk for a clean local build.
- The original public MinIO and mc images returned registry authorization denial. Delivery uses a pinned MinIO source build with a SHA-256-verified archive and Python bucket initialization instead. See the final service check result below.
- Browser visual and interactive QA was not performed: the Sites managed-browser interface (`cua_repl.js`) was unavailable. HTTP template rendering and endpoint interactions are covered by tests.
- A real Stripe test-mode Checkout/Customer Portal upgrade has **not** been performed because no Stripe test account keys, monthly Price or webhook secret were supplied. Synthetic SDK response/signature tests are not a substitute for that external acceptance check. Exact steps are in README.md.
- SMTP provider deliverability, HTTPS reverse proxy, production object storage permissions/encryption, backups and production deployment were not verified. No production deployment was made.

## Service smoke check

**Passed.** MinIO was built from the pinned upstream source and run locally. A separate smoke test used a migrated SQLite database for this service check (MySQL locking was verified separately), actual Mailpit SMTP/API, actual private MinIO S3, actual Redis/RQ, and Tesseract. It exercised:

`email verification → sign in → authenticated upload → hash duplicate → queued RQ job → OCR → editable review → atomic confirmation → spending report → item CSV → authenticated original download → receipt deletion → object cleanup`

The script printed PASS both from the host Python environment and from the built application Docker image. It is included as `tests/integration_smoke.py`. The pinned MinIO **native source build** was verified; its complete multi-stage Docker image and the complete Compose deployment have not been built end to end. Live Stripe remains unverified as described above.
