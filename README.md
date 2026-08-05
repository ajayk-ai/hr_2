# Candidate Document Processor

HR uploads a candidate's onboarding documents in whatever order and with whatever
filenames the scanner produced. The service reads each one with **Google Document
AI**, classifies it and extracts its key fields with **Gemini** (via LangChain),
then returns a ZIP of consistently renamed files in a fixed filing order, plus a
`report.json` describing what it did.

**Nothing is written to disk.** Uploads are held in memory for the life of the
request and the ZIP is streamed back from a buffer.

---

## Architecture

```
React (Vite/TS)                FastAPI
─────────────────              ────────────────────────────────────────────────
FileDropzone                   POST /api/v1/documents/process
  → useDocumentProcessor         → validate + sniff MIME (utils/uploads.py)
  → POST multipart               → per file, concurrently:
  ← ZIP (Blob)                       OCR         (services/ocr.py    → Document AI)
  → fflate reads report.json         classify    (services/classifier.py → Gemini)
  → ResultPanel renders it       → resolve one candidate name across the batch
                                 → deterministic names + order (domain/naming.py)
                                 → in-memory ZIP (services/packaging.py)
```

### Design decisions worth knowing

**The LLM does not choose filenames or sort order.** It returns a document type
and extracted fields; `app/domain/naming.py` turns those into names. Models are
good at reading a document and inconsistent across a batch, and filenames need to
be stable, collision-free and filesystem-safe — that is a job for code you can
unit-test. Prompt drift can change a classification; it cannot change your naming
scheme.

**Filenames are prefixed `01_`, `02_`, …** ZIP entries carry no display order, so
every extractor re-sorts alphabetically. The prefix is what makes the filing order
survive extraction.

**One candidate name for the whole batch.** Documents disagree — an Aadhaar says
`RAVI KUMAR SHARMA`, a resume says `Ravi Sharma`. Votes are weighted by
classification confidence and by how authoritative the document type is for legal
names (identity documents outrank a CV), so the batch does not scatter across
several name prefixes.

**Two confidence thresholds, not one.** Below 0.55 a document is filed as
`Unknown` rather than guessed at. Between 0.55 and 0.75 it is filed as classified
but listed in `needs_review`. A single threshold would force a choice between
discarding a usable classification and hiding the uncertainty; a misfiled Aadhaar
is worse than one a human has to glance at.

**One bad file does not sink the batch.** OCR or classification failures are
recorded per file in `report.json`; every other document still comes back.

**Government IDs are masked by default.** Aadhaar and bank account numbers appear
as `XXXXXXXX1234` in filenames and in the report (`HRDOC_MASK_SENSITIVE_IDS`). PAN
is kept whole, since it is the identifier HR actually files against. Logs are
scrubbed independently by a structlog processor, so an identifier that leaks into
a log call is masked before it reaches a sink.

---

## Setup

Requires Python 3.14 ([uv](https://docs.astral.sh/uv/)) and Node 20+.

### Backend

```bash
uv sync
cp .env.example .env      # then fill in the values below
uv run uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs.

**Google Document AI** — create a *Document OCR* processor in the
[console](https://console.cloud.google.com/ai/document-ai), then set
`HRDOC_GCP_PROJECT_ID`, `HRDOC_DOCAI_LOCATION` (`us` or `eu`) and
`HRDOC_DOCAI_PROCESSOR_ID`. Authenticate with a service account holding the
*Document AI API User* role via `GOOGLE_APPLICATION_CREDENTIALS`, or with
`gcloud auth application-default login` locally. On Cloud Run or GKE, use Workload
Identity instead of a key file.

**Gemini** — get a key from [AI Studio](https://aistudio.google.com/apikey) and set
`HRDOC_GEMINI_API_KEY`.

Both are optional for local UI work: without them the service boots with stub
engines that return `Unknown` for everything and say so in the report, so the
frontend and pipeline can be exercised without credentials or spend.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` and `/health` to
`127.0.0.1:8000`, so the browser stays same-origin in development.

---

## Development

```bash
uv run pytest            # 95 tests
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy              # strict type check

cd frontend && npm run typecheck && npm run build
```

The test suite runs entirely against fake OCR and classifier engines — no network
call, no API spend, no credentials needed.

---

## API

### `POST /api/v1/documents/process`

`multipart/form-data`:

| field                | type            | notes                                             |
| -------------------- | --------------- | ------------------------------------------------- |
| `files`              | file[]          | Required. PDF, JPEG, PNG, GIF, TIFF, BMP, WEBP.   |
| `candidate_name`     | string          | Optional. Overrides the name read from documents. |
| `required_documents` | JSON array      | Optional. Defaults to photo, Aadhaar, PAN, resume, payslip. |

Returns `application/zip` containing the renamed files plus `report.json`.
Response headers: `X-Request-Id`, `X-Files-Failed`, `X-Needs-Review`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/documents/process \
  -H "Authorization: Bearer $HRDOC_API_TOKEN" \
  -F "files=@aadhaar.pdf" -F "files=@payslip_march.pdf" -F "files=@photo.jpg" \
  -F "candidate_name=Ravi Kumar" \
  -o processed.zip
```

Produces:

```
01_RaviKumar_Photograph.jpg
02_RaviKumar_Aadhaar_XXXXXXXX1234.pdf
03_RaviKumar_SalarySlip_202403.pdf
report.json
```

The report records, per input file, the assigned name, document type, confidence,
extracted fields, OCR confidence, page count, any warnings, and any error — plus
batch-level `missing_document_types`, `duplicate_document_types` and
`needs_review`.

### Other endpoints

- `GET /health` — status and which engines are actually wired up (real or stub).
- `GET /api/v1/document-types` — taxonomy, filing order and accepted MIME types,
  so the frontend never hardcodes them.

Errors are `{"error": {"code": "...", "message": "..."}}` with stable codes:
`validation_error`, `payload_too_large`, `unsupported_media_type`,
`unauthenticated`, `ocr_failed`, `classification_failed`.

---

## Configuration

Every setting is an environment variable prefixed `HRDOC_`. See
[`.env.example`](.env.example) for the full list. The ones that matter most:

| Variable                              | Default            | Purpose |
| ------------------------------------- | ------------------ | ------- |
| `HRDOC_API_TOKENS`                    | *(empty)*          | Comma-separated bearer tokens. Empty disables auth — refused in production. |
| `HRDOC_MAX_FILES_PER_REQUEST`         | `25`               | Per-batch file count cap. |
| `HRDOC_MAX_FILE_BYTES`                | `20971520`         | Per-file cap (20 MB). |
| `HRDOC_MAX_TOTAL_BYTES`               | `104857600`        | Per-batch cap (100 MB) — also the peak memory per in-flight request. |
| `HRDOC_OCR_CONCURRENCY`               | `5`                | Parallel Document AI calls. |
| `HRDOC_LLM_CONCURRENCY`               | `5`                | Parallel Gemini calls. |
| `HRDOC_OCR_TEXT_CHAR_BUDGET`          | `12000`            | OCR text sent to the LLM per document (head+tail slice). |
| `HRDOC_MIN_CLASSIFICATION_CONFIDENCE` | `0.55`             | Below this a document is filed as `Unknown`. |
| `HRDOC_REVIEW_CONFIDENCE`             | `0.75`             | Below this a document is filed as classified but listed in `needs_review`. |
| `HRDOC_MASK_SENSITIVE_IDS`            | `true`             | Mask Aadhaar/bank numbers in filenames and the report. |
| `HRDOC_GEMINI_THINKING_BUDGET`        | `0`                | Reasoning tokens per call. Raise if you see systematic misclassification. |

In `HRDOC_ENVIRONMENT=production` the app refuses to start if Document AI, Gemini
or API tokens are unconfigured; `/docs` is also disabled.

---

## Operational notes

**Memory.** Peak memory per in-flight request is bounded by
`HRDOC_MAX_TOTAL_BYTES`, because uploads are deliberately kept out of the
filesystem. Size worker count against that: 4 workers × 100 MB is a 400 MB
worst case. Lower the cap or the worker count on small instances.

**Page limit.** Synchronous Document AI processing is capped at 15 pages for the
OCR processor. Larger PDFs are rejected with an actionable message rather than a
raw gRPC error. Supporting them means moving to batch processing, which requires
GCS staging and therefore persistent storage — a deliberate trade against the
no-storage guarantee.

**Timeouts.** A large batch can take minutes. The request is held open for its
duration, so any reverse proxy in front needs a matching read timeout
(nginx `proxy_read_timeout`, ALB idle timeout). If batches routinely exceed a
couple of minutes, the next step is a job queue with a polled status endpoint —
which reintroduces storage and is why it is not here.

**Retries.** Transient Document AI and Gemini failures retry three times with
exponential backoff. Non-transient failures (`INVALID_ARGUMENT`,
`PERMISSION_DENIED`) are not retried; they are configuration errors.

**TLS behind a corporate proxy.** Networks that intercept and re-sign TLS present
a private root CA that Python's bundled `certifi` does not carry, so outbound
calls fail with `CERTIFICATE_VERIFY_FAILED: self-signed certificate in
certificate chain`. `HRDOC_USE_SYSTEM_TRUST_STORE` (on by default) verifies
against the OS certificate store instead, where that CA already lives. The symptom
is lopsided — Document AI keeps working over gRPC while Gemini fails over HTTPS —
which makes it look like a Gemini problem rather than a network one. Never work
around it by disabling verification.

**Logging.** Structured via structlog, JSON when `HRDOC_LOG_JSON=true`. Every
record carries the request id, which is the same id used in `report.json` and the
`X-Request-Id` response header.

**Adding a document type.** Add the member to `DocumentType`, place it in
`CANONICAL_ORDER`, write a `TYPE_HINTS` entry, and add an identifier branch in
`naming.build_identifier` if it needs one. The prompt, the report and the ordering
all read from that one module.

---

## Known limitations

- The frontend token lives in a `VITE_` variable, which is embedded in the built
  bundle and readable by anyone with the page. That is acceptable behind SSO/VPN
  for an internal tool; a public deployment needs a server-side proxy holding the
  token.
- No rate limiting. Each request costs real Document AI and Gemini spend — put a
  limiter at the gateway before exposing this widely.
- Classification quality on Indian documents has not been measured against a
  labelled set. Before trusting it unsupervised, assemble 50–100 real scans per
  type and measure per-type precision; the `confidence` values in `report.json`
  are what you would calibrate the threshold against.
