from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Codex Server", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "s3_bucket": settings.S3_BUCKET,
    }
