"""`python -m app` runs the server on BIND_ADDRESS:PORT (127.0.0.1:8090 by default)."""

import logging

import uvicorn

from .config import Settings
from .main import create_app

settings = Settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
uvicorn.run(create_app(settings), host=settings.bind_address, port=settings.port, proxy_headers=False)
