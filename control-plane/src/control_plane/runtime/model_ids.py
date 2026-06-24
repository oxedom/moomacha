"""Normalize the project's model ids to LangChain provider-prefixed ids.

The legacy OpenAI loop uses bare ids like "gpt-4o"; DeepAgents/LangChain expects
"<provider>:<model>". A bare id is assumed to be OpenAI; anything already
containing a ":" is treated as provider-prefixed and returned unchanged.
"""


def normalize_model_id(model_id: str) -> str:
    if ":" in model_id:
        return model_id
    return f"openai:{model_id}"
