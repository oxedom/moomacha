from typing import Any


def default_client_factory(api_key: str, base_url: str | None) -> Any:
    from openai import AsyncOpenAI

    if base_url:
        return AsyncOpenAI(api_key=api_key, base_url=base_url)
    return AsyncOpenAI(api_key=api_key)
