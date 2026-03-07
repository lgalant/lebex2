from fastapi import APIRouter

#from lebex.slack.router import router as router_slack
from lebex.whatsapp.router import router as router_whatsapp

router = APIRouter()

#router.include_router(router=router_slack)
router.include_router(router=router_whatsapp)


@router.get("/healthz")
def health():
    return {"status": "ok"}
