from fastapi import APIRouter

from lebex.parse.router import router as router_parse


router = APIRouter(prefix="/v1")
router.include_router(router=router_parse)
