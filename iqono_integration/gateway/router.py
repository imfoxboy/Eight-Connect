from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

try:
    from .handler import handle_pay, handle_status, handle_callback
    from ..schemas.payment import PayRequest
    from ..schemas.status import StatusRequest
    from ..utils.db import init_db
    from ..utils.logger import logger
except ImportError:  # pragma: no cover
    from gateway.handler import handle_pay, handle_status, handle_callback
    from schemas.payment import PayRequest
    from schemas.status import StatusRequest
    from utils.db import init_db
    from utils.logger import logger

router = APIRouter()


@router.on_event("startup")
async def startup():
    await init_db()
    logger.info("IQONO Checkout integration started — DB initialized")


@router.post("/pay", summary="Create IQONO Checkout session")
async def pay(req: PayRequest):
    return await handle_pay(req)


@router.post("/status", summary="Poll IQONO payment status")
async def status(req: StatusRequest):
    return await handle_status(req)


@router.post("/callback", summary="Receive IQONO Checkout webhook (application/x-www-form-urlencoded)")
async def callback(request: Request):
    form_data = dict(await request.form())
    result = await handle_callback(form_data)
    return PlainTextResponse(result)


@router.get("/health")
async def health():
    return {"status": "ok", "provider": "iqono", "integration": "checkout"}
