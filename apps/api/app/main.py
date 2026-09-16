from fastapi import FastAPI

from app.api.v1 import api_router

app = FastAPI(title="Finance Opinion Radar", version="0.1.0")
app.include_router(api_router)


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
