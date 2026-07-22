"""`python -m web` — run the agent service on 127.0.0.1:8001."""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run("web.app:app", host="127.0.0.1", port=8001, reload=False)
