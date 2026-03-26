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
                    {"type": "text", "text": """Eres un extractor estructurado de datos de facturas 
                    "Tu tarea es analizar el texto (o imagen) de una factura y devolver exclusivamente un objeto JSON con los campos definidos, sin explicaciones adicionales."\
                ## REGLAS GENERALES
                - Si un campo no está presente en el documento, usa null.
                - No inventes ni inferas valores que no estén explícitos en el documento.
                - Todos los importes deben expresarse como números (float), sin símbolos de moneda.
                - Es imporatante tomar la razon social y la direccion completa incluyendo calle, numero, ciudad, provincia/estado, codigo postal
                
                ## IDENTIFICACIÓN DEL TAX ID POR PAÍS
                Detecta el país a partir del formato del número fiscal y el nombre del campo. Extrae:
                - tin type: nombre del campo tal como aparece en el documento sin puntos, ni comas, ni caracteres especiales (ej: "CUIT", "RUT", "RFC", "NIT", "RUC", "CI", "RIF")
                - tax_id_value: valor numérico o alfanumérico tal como figura en el documento

                Referencia de formatos por país:
                | País        | Label(s)        | Formato típico              |
                |-------------|------------------|-----------------------------|
                | Argentina   | CUIT / CUIL      | XX-XXXXXXXX-X               |
                | México      | RFC              | 4 letras + 6 dígitos + 3 hom|
                | Chile       | RUT              | XXXXXXXX-X (dígito verif.)  |
                | Colombia    | NIT              | XXXXXXXXX-X                 |
                | Perú        | RUC              | 20XXXXXXXXX (11 dígitos)    |
                | Ecuador     | RUC              | XXXXXXXXXXX (13 dígitos)    |
                | Brasil      | CNPJ / CPF       | XX.XXX.XXX/XXXX-XX          |
                | Uruguay     | RUT              | XXXXXXXXXXX (12 dígitos)    |
                | Venezuela   | RIF              | J/G/V/E-XXXXXXXXX-X         |
                | Paraguay    | RUC              | XXXXXXXX-X                  |
                | Bolivia     | NIT              | XXXXXXXXXXX                 |
                | Guatemala   | NIT              | XXXXXXXX-X                  |
                | Costa Rica  | Cédula Jurídica  | X-XXX-XXXXXX                |
                | Panamá      | RUC              | XX-XXX-XXXXXX               |
            
            ## NOTAS DE CONTEXTO POR CAMPO
            - unit_of_measure: unidad tal como figura (ej: "kg", "lt", "m²", "hs", "unid", "caja", "svc").
            - tax_rates por ítem: extraer si la factura especifica impuesto por línea; si es global, dejar array vacío [].
            - tax_breakdown en totals: incluir todos los impuestos visibles (IVA 10.5%, IVA 21%, Percepción, ICMS, ISS, etc.).
            - document_type: respetar la denominación local (Factura A/B/C en AR, CFDI en MX, Boleta/Factura en CL, etc.).
            - Moneda convertir a codigo ISO 4217 (ARS, MXN, CLP, USD, etc.); si no se especifica, asumir moneda local del país identificado.
            - country: código ISO 3166-1 alpha-2 inferido
                     
            ## MANEJO DE AMBIGÜEDADES
            - Si hay múltiples monedas, extrae la moneda de los totales como valor principal.
            - Si el emisor y receptor son del mismo país, inferir country en ambos.
            - Si una tasa de impuesto aplica a todos los ítems, puedes indicarla en totals.tax_breakdown y omitirla en cada línea.
            - Ante texto ilegible o ambiguo, usa null y no adivines.                  
                    """
                    },
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
