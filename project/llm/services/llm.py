"""Backend-only boundary for OpenAI-compatible providers."""

import asyncio
import logging
import re
import httpx
from urllib.parse import urlsplit
from collections.abc import Sequence

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessageParam

from ..config import LLMConfigurationError, LLMSettings

logger = logging.getLogger(__name__)
_SDK_CLIENT=AsyncOpenAI


class LLMError(RuntimeError):
    """Safe error for the backend: no provider response, headers or secret values."""

    def __init__(self, code: str, status_code: int | None = None) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(f"LLM request failed: {code}.")


def _failure(code: str, status_code: int | None = None) -> LLMError:
    logger.warning("LLM failure code=%s status=%s", code, status_code)
    return LLMError(code, status_code)


class LLMService:
    def __init__(self, settings: LLMSettings, *, transport=None):
        self.settings=settings;self._closed=False;self.client_creations=1;self.close_count=0
        options=dict(timeout=settings.timeout_seconds,follow_redirects=False,
            limits=httpx.Limits(max_connections=12,max_keepalive_connections=6,keepalive_expiry=30))
        if transport is not None:options['transport']=transport
        sdk_options=dict(api_key=settings.api_key,base_url=settings.base_url,
            timeout=settings.timeout_seconds,max_retries=0)
        if AsyncOpenAI is _SDK_CLIENT or transport is not None:
            self.http_client=httpx.AsyncClient(**options);sdk_options['http_client']=self.http_client
        else:self.http_client=None
        self.client=AsyncOpenAI(**sdk_options)

    @property
    def is_closed(self):return self._closed

    async def close(self):
        if self._closed:return
        self._closed=True;self.close_count+=1
        await self.client.close()

    async def complete(self,messages,**kwargs):
        if self._closed:raise LLMError('closed')
        return await call_llm(messages,settings=self.settings,service=self,**kwargs)


_default=None
_default_key=None
_default_factory=None
_default_settings=None


def _settings():
    global _default_settings
    if _default_settings is None:
        _default_settings = LLMSettings.from_env()
    return _default_settings

def _service(config):
    global _default,_default_key,_default_factory
    key=(config.api_key,config.base_url,config.model,config.timeout_seconds);factory=id(AsyncOpenAI)
    if _default is None or _default.is_closed or _default_key!=key or _default_factory!=factory:
        _default=LLMService(config);_default_key=key;_default_factory=factory
    return _default

async def close():
    global _default,_default_key,_default_factory,_default_settings
    if _default is not None:await _default.close()
    _default=None;_default_key=None;_default_factory=None;_default_settings=None


async def call_llm(
    messages: Sequence[ChatCompletionMessageParam],
    *,
    settings: LLMSettings | None = None,
    json_mode: bool = False,
    max_tokens: int | None = None,
    extraction: bool = False,
    metrics: dict | None = None,
    response_schema: dict | None = None,
    service: LLMService | None = None,
) -> str:
    """Generate a text reply. Call only from backend business logic, never a channel adapter."""
    try:
        config = settings or _settings()
    except LLMConfigurationError:
        logger.warning("LLM configuration invalid; check server environment.")
        raise
    if not messages:
        raise ValueError("messages must not be empty.")
    strict=response_schema is not None and urlsplit(config.base_url).hostname=='api.groq.com' and config.model in {'openai/gpt-oss-20b','openai/gpt-oss-120b'}
    response_format={'type':'json_schema','json_schema':{'name':'work_extraction','strict':True,'schema':response_schema}} if strict else {'type':'json_object'}

    # SDK debug logging may include request/response bodies. Keep it disabled here.
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    managed=service or _service(config)
    if managed.is_closed:raise LLMError('closed')
    attempt=0
    try:
        # One outer deadline: the recovery attempt never doubles user wait.
        async with asyncio.timeout(config.timeout_seconds):
            while True:
                attempt+=1
                try:
                    response = await managed.client.chat.completions.create(
                        model=config.model,messages=list(messages),
                        **({'max_tokens':max_tokens} if max_tokens is not None else {}),
                        **({'reasoning_effort':'medium','temperature':0} if extraction and config.model.startswith('openai/gpt-oss') else {}),
                        **({'response_format':response_format} if json_mode else {}),
                    )
                    break
                except APITimeoutError:
                    raise _failure("timeout") from None
                except APIConnectionError:
                    if attempt==1:
                        logger.warning("LLM retry code=connection attempt=2")
                        continue
                    raise _failure("connection") from None
                except APIStatusError as error:
                    detail=error.body if isinstance(error.body,dict) else {}
                    detail=detail.get('error',detail)
                    if metrics is not None and isinstance(detail,dict):
                        metrics['http_status']=error.status_code
                        if detail.get('code')=='json_validate_failed':
                            metrics['provider_code']='json_validate_failed'
                        limit=re.search(r'(tokens|requests) per (minute|day).*?Limit ([\d.]+), Used ([\d.]+), Requested ([\d.]+)',str(detail.get('message','')),re.I)
                        if limit:metrics['provider_limit']=dict(zip(['unit','period','limit','used','requested'],limit.groups()))
                    code={401:"authentication",403:"permission",402:"balance",429:"rate_limit"}.get(error.status_code,"api_error")
                    if isinstance(detail,dict) and detail.get('code')=='json_validate_failed':code='invalid_response'
                    if attempt==1 and error.status_code in {500,502,503,504}:
                        logger.warning("LLM retry status=%s attempt=2",error.status_code)
                        continue
                    raise _failure(code,error.status_code) from None
                except OpenAIError:
                    raise _failure("invalid_response") from None
    except TimeoutError:
        raise _failure("timeout") from None

    if metrics is not None:
        metrics.update(attempt_count=attempt,retry_count=max(0,attempt-1))
        usage=getattr(response,'usage',None)
        metrics.update(model=getattr(response,'model',config.model),usage=usage.model_dump() if usage else None,
                       reasoning_effort='medium' if extraction and config.model.startswith('openai/gpt-oss') else None,strict_schema=strict,
                       finish_reason=response.choices[0].finish_reason if response.choices else None)
    if not response.choices or not response.choices[0].message.content:
        raise _failure("empty_response")
    return response.choices[0].message.content
