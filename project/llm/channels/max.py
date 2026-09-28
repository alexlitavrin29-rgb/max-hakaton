"""Small MAX HTTP adapter. No LLM, database or dialogue logic here."""

import asyncio
import logging
import time
import ssl
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)
_TRANSIENT_HTTP = {429, 500, 502, 503, 504}


class MaxError(RuntimeError):
    def __init__(self, code: str, status: int | None = None):
        self.code, self.status = code, status
        super().__init__(f"MAX: {code} ({status})")


class MaxClient:
    def __init__(self, token: str, base_url: str = "https://platform-api2.max.ru", *, transport=None,
                 min_send_interval=0.6):
        if not token.strip():
            raise ValueError("MAX_BOT_TOKEN is required")
        # Never permit a configured host to receive credentials accidentally.
        if base_url not in {"https://platform-api.max.ru", "https://platform-api2.max.ru"}:
            raise ValueError("Unsupported MAX API host")
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        context = ssl.create_default_context()
        if base_url == "https://platform-api2.max.ru":
            for cert in (Path(__file__).resolve().parents[1] / "certs").glob("*.crt"):
                context.load_verify_locations(cert)
        options=dict(base_url=base_url,headers={"Authorization":token},timeout=40,verify=context,
                     follow_redirects=False,limits=httpx.Limits(max_connections=12,max_keepalive_connections=6,keepalive_expiry=30))
        if transport is not None:options['transport']=transport
        self.client=httpx.AsyncClient(**options)
        self._send_lock = asyncio.Lock()
        self._last_send = 0.0
        self._min_send_interval=min_send_interval;self._closed=False;self.close_count=0

    async def close(self):
        if self._closed:return
        self._closed=True;self.close_count+=1
        await self.client.aclose()

    async def request(self, method, path, **kwargs):
        safe_retry=method.upper()=="GET"
        try:
            async with asyncio.timeout(40):
                for attempt in range(2):
                    try:
                        response = await self.client.request(method, path, **kwargs)
                        response.raise_for_status()
                        data = response.json()
                        if not isinstance(data, dict):raise ValueError()
                        if data.get("success") is False:raise MaxError("rejected")
                        return data
                    except httpx.HTTPStatusError as error:
                        status=error.response.status_code
                        if safe_retry and attempt==0 and status in _TRANSIENT_HTTP:
                            logger.warning("MAX safe read retry status=%s attempt=2",status)
                            continue
                        raise MaxError("http",status) from None
                    except httpx.TimeoutException:
                        raise MaxError("timeout") from None
                    except httpx.RequestError:
                        if safe_retry and attempt==0:
                            logger.warning("MAX safe read retry code=connection attempt=2")
                            continue
                        raise MaxError("connection") from None
                    except ValueError:
                        raise MaxError("invalid_response") from None
        except TimeoutError:
            raise MaxError("timeout") from None

    async def send(self, user_id: int, message: dict, metrics=None):
        # Serial global limit also protects each individual dialogue (<2 sends/s).
        total_started=time.perf_counter();queue_started=time.perf_counter()
        async with self._send_lock:
            queue_ms=(time.perf_counter()-queue_started)*1000
            delay=max(0,self._min_send_interval-(time.monotonic()-self._last_send))
            sleep_started=time.perf_counter();await asyncio.sleep(delay)
            sleep_ms=(time.perf_counter()-sleep_started)*1000
            try:
                http_started=time.perf_counter()
                return await self.request("POST", "/messages", params={"user_id": user_id}, json=message)
            finally:
                http_ms=(time.perf_counter()-http_started)*1000
                self._last_send = time.monotonic()
                if metrics is not None:metrics.update(delivery_queue_ms=queue_ms,delivery_sleep_ms=sleep_ms,
                    delivery_http_ms=http_ms,delivery_total_ms=(time.perf_counter()-total_started)*1000)

    async def answer_callback(self, callback_id: str, body: dict):
        return await self.request("POST", "/answers", params={"callback_id": callback_id}, json={"message":body})

    async def updates(self, marker=None):
        params = {"timeout": 20, "limit": 100,
                  "types": "bot_started,message_created,message_callback,bot_stopped"}
        if marker is not None:
            params["marker"] = marker
        data=await self.request("GET", "/updates", params=params)
        if not isinstance(data.get("updates"),list):raise MaxError("invalid_response")
        return data


def callback(label: str, payload: str) -> dict:
    return {"type": "callback", "text": label[:128], "payload": payload}


def link(label: str, url: str) -> dict:
    return {"type": "link", "text": label[:128], "url": url}


def message(text: str, buttons: list[dict] | None = None, format: str | None = None) -> dict:
    body = {"text": text[:3900]}
    if format:
        body["format"] = format
    if buttons:
        # One button per row works with long Russian labels on mobile.
        body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": [[b] for b in buttons]}}]
    return body
