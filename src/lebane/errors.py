import functools

import httpx


def translate_httpx_errors(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except httpx.TimeoutException as exc:
            raise LebaneTimeoutError(
                message=str(exc),
                request=exc.request,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LebaneError(
                message=str(exc),
                request=exc.request,
                response=exc.response,
            ) from exc

    return wrapper


class LebaneTimeoutError(httpx.ReadTimeout): ...


class LebaneError(httpx.HTTPStatusError): ...
