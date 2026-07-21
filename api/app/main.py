from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import check_platform_ready
from app.router import router

app = FastAPI(title="DecisionHarbor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    if not check_platform_ready():
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "error", "error": {"code": "DB_UNAVAILABLE", "message": "数据库未就绪"}})
    return {"status": "ok"}
