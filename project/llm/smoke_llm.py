"""One synthetic backend request; prints neither keys nor the response body."""

import asyncio

from .config import LLMConfigurationError
from .services.llm import LLMError, call_llm


async def main() -> int:
    try:
        reply = await call_llm([
            {"role": "system", "content": "Ты помощник внутри MAX-бота. Это техническая проверка связи."},
            {"role": "user", "content": "Ответь одним словом: ОК."},
        ])
    except LLMConfigurationError:
        print("ERROR: check POLZA_API_KEY, LLM_BASE_URL, LLM_MODEL and LLM_TIMEOUT_SECONDS.")
        return 1
    except LLMError as error:
        print(f"ERROR: {error.code}; HTTP status: {error.status_code or 'none'}")
        return 1
    print(f"OK: LLM returned a text response ({len(reply)} characters).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
