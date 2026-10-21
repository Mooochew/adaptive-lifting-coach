# Adaptive Lifting Coach

An AI strength coach that builds a 4-week training block, then adapts it every week based on what you actually lifted and how hard it felt (RPE), explaining every change.

- **Frontend:** Next.js + TypeScript (`frontend/`)
- **Backend:** Python FastAPI: training math, rules, and (soon) the LLM calls (`backend/`)

> Work in progress. Prompt-engineering write-up and screenshots coming soon.

## Requirements

| Tool | Version | Check |
|---|---|---|
| Python | 3.11 or newer (developed on 3.12) | `python --version` (Windows: `py --version`) |
| Node.js | 22.12+ or 24+ (developed on 24) | `node --version` |
| npm | 10+ (comes with Node) | `npm --version` |

## Quick start

You need **two terminals**: one for the backend, one for the frontend.

### 1. Backend (terminal 1)

Windows (PowerShell):
```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
uvicorn app.main:app --reload     # http://localhost:8000  (API docs: /docs)
```

macOS / Linux:
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

All Python packages install inside `backend/.venv`, never globally.

### 2. Frontend (terminal 2)

```bash
cd frontend
npm ci                            # exact versions from package-lock.json
npm run dev                       # http://localhost:3000
```

Open **http://localhost:3000**. The frontend talks to the backend at `http://localhost:8000` by default; to change that, copy `frontend/.env.example` to `frontend/.env.local` and edit it.

## Tests

Backend (from `backend/`, venv active): training rules, progression, guards, templates, API
```bash
pytest
```

Frontend (from `frontend/`): api client, storage, store, workout helpers
```bash
npm test
```

## Troubleshooting

| Problem | Fix |
|---|---|
| Page says "Backend not reachable" | Start the backend (step 1) and keep that terminal open. |
| `Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again. |
| `npm ci` warns about the engine / Node version | Install Node 24 (e.g. via nvm: `nvm install 24`; `frontend/.nvmrc` pins it). |
| `npm install <pkg>` crashes with `reading 'edgesOut'` | npm 11.5.x bug when adding packages. Use `npx npm@latest install <pkg>`. Plain `npm ci` is unaffected. |
| Port 3000 or 8000 already in use | Stop the other process, or pick other ports: `uvicorn app.main:app --port 8001` and `npm run dev -- -p 3001`, then set `NEXT_PUBLIC_API_URL=http://localhost:8001` in `frontend/.env.local` and `FRONTEND_ORIGIN=http://localhost:3001` in `backend/.env` (see `backend/.env.example`). |

More detail: [frontend/README.md](frontend/README.md).
