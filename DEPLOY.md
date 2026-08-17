# Deploying to another local machine

Native setup (uv + npm), mirroring local dev. See [`README.md`](README.md) for
the full config reference.

## 1. Get the code onto the new machine

```bash
git clone https://github.com/ajayk-ai/hr_2.git
cd hr_2
```

> Note: this machine currently has uncommitted changes in `app/` and
> `frontend/src/` that were deliberately **not** pushed. `git clone` will not
> include them — commit and push separately if the other machine needs that
> work too.

## 2. Run the setup script

```powershell
.\scripts\setup.ps1
```

This checks for `uv`/Node 20+, runs `uv sync`, creates `.env` from
`.env.example` and `frontend/.env.local` from `frontend/.env.example` (only if
they don't already exist), and runs `npm install`.

Requires on the target machine: [uv](https://docs.astral.sh/uv/getting-started/installation/)
and Node 20+. Python 3.14 itself does not need to be preinstalled — `uv sync`
provisions it via `.python-version`.

## 3. Move credentials over — do this out-of-band, never via git

`.env` and the GCP service-account JSON are both gitignored and contain live
secrets (Gemini API key, service-account private key). Copy them to the new
machine over a channel you trust (USB drive, password manager's secure file
storage, `scp`/encrypted transfer) — not email, chat, or a public repo.

Files to move from this machine:

- `.env` → same path in the cloned repo, **or** re-fill `.env` on the target
  from `.env.example` using the values below (get the actual values from this
  machine's `.env`, don't guess them):
  - `HRDOC_GCP_PROJECT_ID`, `HRDOC_DOCAI_LOCATION`, `HRDOC_DOCAI_PROCESSOR_ID`
  - `HRDOC_GEMINI_API_KEY`
  - `GOOGLE_APPLICATION_CREDENTIALS` — update this path once step below is done
- The service-account key JSON currently at
  `C:\Users\11326\.gcp\hr-ocr-project-505504-a2141beb7d25.json` → copy to an
  equivalent out-of-repo location on the new machine (e.g. `C:\Users\<user>\.gcp\...json`),
  then point `GOOGLE_APPLICATION_CREDENTIALS` in the new `.env` at that path.

Both machines will share the same GCP project/quota and the same API key —
fine for two machines used by the same team, but note it if you care about
per-machine usage attribution or want to revoke one independently later.

Frontend: `frontend/.env.local` normally stays empty in dev (Vite proxies to
`127.0.0.1:8000`), so nothing to copy there unless you customized it.

## 4. Run it

`scripts/setup.ps1` already ran `npm run build`, producing `frontend/dist`.
`app/main.py` mounts that directory automatically ([app/main.py:176-196](app/main.py#L176-L196))
— if it's present, one uvicorn process serves both the UI and the API:

```powershell
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
# → http://127.0.0.1:8000        (UI)
# → http://127.0.0.1:8000/docs   (API docs, disabled in production)
# → http://127.0.0.1:8000/health
```

Or just double-click **`scripts\run.bat`** — no admin, no PowerShell execution
policy to worry about. It runs the same command in a console window; closing
the window stops the server, and it needs to be started again after a reboot.

Nothing else to run — no separate frontend server, no proxy. Because the UI
and API are same-origin here, `frontend/.env.local`'s `VITE_API_BASE_URL` can
stay empty (requests resolve to relative paths).

If `frontend/dist` is missing or stale, the app still starts but logs
`app.frontend_missing` and serves API-only; rebuild with `cd frontend; npm run build`
and restart.

**Active frontend development** (hot reload) still uses the two-process dev
flow instead:

```powershell
# Terminal 1
uv run uvicorn app.main:app --reload
# Terminal 2
cd frontend; npm run dev   # → http://localhost:5173, proxies /api and /health
```

### Making it persistent (production, needs admin)

`scripts\run.bat` and the raw uvicorn command above only last as long as their
window stays open — they won't survive logout or a reboot, and nothing
restarts them if they crash. For a server that should just stay up
unattended, register it as a Windows Scheduled Task instead (this step does
require an elevated/Administrator PowerShell — there's no way around that for
a task that starts before anyone logs in, regardless of `.bat` vs `.ps1`):

```powershell
# From an elevated (Administrator) PowerShell, after scripts\setup.ps1 has run:
.\scripts\register-task.ps1
```

This creates a task (`HRDocProcessor`) that starts at boot under the SYSTEM
account (no user needs to be logged in), restarts automatically on crash, and
logs stdout/stderr to `logs\uvicorn.log`. Manage it with:

```powershell
Start-ScheduledTask   -TaskName HRDocProcessor
Stop-ScheduledTask    -TaskName HRDocProcessor
Get-ScheduledTask     -TaskName HRDocProcessor | Get-ScheduledTaskInfo
Unregister-ScheduledTask -TaskName HRDocProcessor -Confirm:$false   # remove it
```

SYSTEM needs read access to `.env` and the GCP service-account JSON — fine
under default NTFS permissions unless they were locked down further.

## 5. Verify

- `GET http://127.0.0.1:8000/health` should report Document AI and Gemini as
  real engines (not stub) once credentials are in place.
- `uv run pytest` — 178 tests, no credentials needed (fake engines).
- Open `http://127.0.0.1:8000/` and upload a real document; confirm a ZIP with
  renamed files + `report.json` comes back.

## Optional: Docker instead

A `Dockerfile` exists for the backend. Its header comment currently describes
building the frontend separately and serving it via a CDN/nginx — now that
`app/main.py` serves `frontend/dist` itself, a build step that runs
`npm run build` before `uv sync` in the image would let the same container
serve both. Not set up by `scripts/setup.ps1`; ask if you want that added.
