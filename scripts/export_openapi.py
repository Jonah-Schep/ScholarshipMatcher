"""Write the API's OpenAPI schema to frontend/openapi.json (the source of the front end's TypeScript types).

Run after changing a route or a schema in scholarmatch/api, then `npm run gen:api` in frontend/.
No server, network or LLM key needed."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SM_LLM_PROVIDER", "offline")

from scholarmatch.api.app import create_app  # noqa: E402

if __name__ == "__main__":
    out = ROOT / "frontend" / "openapi.json"
    out.write_text(json.dumps(create_app().openapi(), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")
