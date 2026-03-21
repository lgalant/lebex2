import base64
import mimetypes
import posixpath
from io import BytesIO
from typing import BinaryIO
from urllib.parse import urlparse

import httpx
from fastapi import UploadFile
from openai import AsyncOpenAI
from pdf2image import convert_from_bytes
from PIL import Image
from starlette.datastructures import Headers

from .schemas import Invoice


async def download_file(url: str) -> UploadFile:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()

    parsed_url = urlparse(url)
    filename = posixpath.basename(parsed_url.path) or "downloaded_file"

    headers = Headers(response.headers)
    if headers.get("content-type") in (None, "binary/octet-stream"):
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or "binary/octet-stream"
        headers = Headers(dict(headers) | {"content-type": content_type})

    return UploadFile(
        BytesIO(initial_bytes=response.read()),
        filename=filename,
        headers=headers,
    )


def concat_images(*images: Image.Image) -> Image.Image:
    """Generate composite of all supplied images."""
    # Get the widest width.
    width = max(image.width for image in images)
    # Add up all the heights.
    height = sum(image.height for image in images)
    composite = Image.new("RGB", (width, height))
    # Paste each image below the one before it.
    y = 0
    for image in images:
        composite.paste(image, (0, y))
        y += image.height
    return composite


def pdf_to_image(file: BinaryIO) -> Image.Image:
    pages = [
        preprocess_image(image=image)
        for image in convert_from_bytes(file.read())
    ]
    return concat_images(*pages)


def preprocess_image(image: Image.Image, max_size: int = 1024) -> Image.Image:
    image = image.convert("L")
    image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return image


def encode_image(image: Image.Image) -> str:
    buffer: BytesIO = BytesIO()
    image.save(buffer, format="JPEG")
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("utf-8")


async def invoice_to_model(file: BinaryIO, content_type: str) -> Invoice:
    image: Image.Image
    if content_type == "application/pdf":
        image = pdf_to_image(file=file)
    else:
        image = Image.open(file)
        image = preprocess_image(image=image)

    base64_image = encode_image(image=image)

    client = AsyncOpenAI()
    completion = await client.beta.chat.completions.parse(
        model="gpt-4.1-mini-2025-04-14",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "This is an invoice. "\
                    "The top section is usually the seller information including invoice number, date and invoice type (A,B,C,X), the middle part is the buyer information"\
                    "The most important information on these sections is CUIT (like tax id) and it is usually xx-xxxxxxxx-x format, sometimes without dashes"\
                    "Also grab the company name (razon social),  full address information , and tax status"
                    "The bottom section is the list of items, each item has a description, quantity, unit price, uom,  taxes and total amount."\
                    "Also grab discount info, exchange rate, tax rate and amounts"
                    "If there is a currency code convert to ISO"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}",
                        },
                    },
                ],
            }
        ],
        response_format=Invoice,
    )
    invoice = completion.choices[0].message.parsed
    assert isinstance(invoice, Invoice)
    return invoice
