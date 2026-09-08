"""Fail-closed access control shared by custom and built-in API routes."""

from __future__ import annotations

import asyncio
import secrets

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from agent import config as cfg


def auth_error(headers: Headers) -> JSONResponse | None:
    expected = cfg.api_admin_token()
    if not expected or not expected.isascii() or not 32 <= len(expected) <= 256:
        return JSONResponse(
            {"error": "задайте на сервере API_ADMIN_TOKEN: 32–256 ASCII-символов"},
            status_code=503,
        )
    values = headers.getlist("authorization")
    scheme, _, provided = (values[0] if len(values) == 1 else "").partition(" ")
    if (
        scheme.lower() == "bearer"
        and provided.isascii()
        and secrets.compare_digest(provided, expected)
    ):
        return None
    return JSONResponse(
        {"error": "API требует Bearer-токен из API_ADMIN_TOKEN"},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )


class ApiSecurityMiddleware:
    """LangGraph imports this middleware from http.app for the entire server.

    Buffer bounded request bodies before dispatch so a streaming/chunked body
    cannot bypass the size limit or cause partial mutations before rejection.
    Response streams are forwarded without buffering.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        async def reject(status, message):
            await JSONResponse({"error": message}, status_code=status)(scope, receive, secure_send)

        headers = Headers(scope=scope)
        if denied := auth_error(headers):
            await denied(scope, receive, secure_send)
            return
        limit = cfg.api_max_request_bytes()
        lengths = headers.getlist("content-length")
        if lengths:
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
                await reject(400, "некорректный Content-Length")
                return
            if len(lengths[0]) > 10 or int(lengths[0]) > limit:
                await reject(413, "тело запроса превышает API_MAX_REQUEST_BYTES")
                return
        body = bytearray()
        try:
            async with asyncio.timeout(30):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > limit:
                        await reject(413, "тело запроса превышает API_MAX_REQUEST_BYTES")
                        return
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await reject(408, "истекло время чтения запроса")
            return
        if lengths and int(lengths[0]) != len(body):
            await reject(400, "Content-Length не соответствует телу запроса")
            return
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, secure_send)
