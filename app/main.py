from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from app.config import settings
from app.logging_config import configure_logging

configure_logging()
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info("app_startup", environment=settings.app_env)
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
