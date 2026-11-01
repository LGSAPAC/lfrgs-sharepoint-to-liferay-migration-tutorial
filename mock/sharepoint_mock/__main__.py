"""python -m sharepoint_mock  (MOCK_MODE, MOCK_SITE_URL, PORT; see app.py)."""

import os

import uvicorn

from .app import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8091")), log_level="warning")
