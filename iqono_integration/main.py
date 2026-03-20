try:
    from .gateway.router import router as gateway_router
except ImportError:  # pragma: no cover
    from gateway.router import router as gateway_router

from fastapi import FastAPI

app = FastAPI(
    title="IQONO Gateway Connector",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(gateway_router)
