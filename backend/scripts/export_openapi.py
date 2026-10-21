"""Write the API's OpenAPI schema to backend/openapi.json (no server needed).

The frontend generates its TypeScript types from this file:
    python scripts/export_openapi.py          (from backend/, venv active)
    npm run gen:types                         (from frontend/)
"""

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402

out = BACKEND / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
print(f"Wrote {out}")
