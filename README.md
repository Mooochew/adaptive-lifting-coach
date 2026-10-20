# Adaptive Lifting Coach

An AI strength coach that writes a 4-week training block, then adapts it every week based on what you actually lifted and how hard it felt (RPE), explaining every change.

- **Frontend:** Next.js + TypeScript (`frontend/`)
- **Backend:** Python FastAPI, which handles the LLM calls and training math (`backend/`)

> Work in progress. Setup instructions, prompt-engineering write-up, and screenshots coming soon.

## Backend setup (Windows / PowerShell)

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
```

All Python packages are installed inside `backend/.venv`, never globally.

With the venv active, from `backend/`:

```powershell
pytest                              # run tests
uvicorn app.main:app --reload       # start API at http://localhost:8000 (docs at /docs)
```
