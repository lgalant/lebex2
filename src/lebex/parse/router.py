import json
from typing import cast

from fastapi import APIRouter
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi import status

from .schemas import Invoice
from .schemas import InvoiceRequest
from .services import download_file
from .services import invoice_to_model


router = APIRouter(prefix="/parse", tags=["parse"])


@router.post(
    "/invoice",
    status_code=status.HTTP_200_OK,
    summary="Parse an Invoice to JSON",
)
async def parse_invoice(
    file: None | UploadFile = File(None),
    data: None | str = Form(
        None,
        description='Has to be a JSON in the form {"url": "<url>"}',
    ),
) -> Invoice:
    if (file and data) or (not file and not data):
        raise HTTPException(
            status_code=400,
            detail="You must provide either a file OR a URL (but not both).",
        )

    if data:
        jdata = InvoiceRequest(**json.loads(data))
        file = cast(
            UploadFile, await download_file(url=jdata.url.unicode_string())
        )

    assert file

    if not file.content_type or (
        not file.content_type.startswith("image/")
        and file.content_type not in ("application/pdf",)
    ):
        raise HTTPException(
            status_code=400,
            detail=f"File must be an image, got {file.content_type}.",
        )

    return await invoice_to_model(
        file=file.file, content_type=file.content_type
    )
