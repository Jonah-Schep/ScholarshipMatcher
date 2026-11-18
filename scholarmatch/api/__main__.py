"""Serve the API:  python -m scholarmatch.api   (SM_API_HOST, default 127.0.0.1; SM_API_PORT, default 8000)."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("scholarmatch.api.app:app", host=os.environ.get("SM_API_HOST", "127.0.0.1"),
                port=int(os.environ.get("SM_API_PORT", 8000)))
