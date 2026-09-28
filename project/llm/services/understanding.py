"""LLM extracts user intent; it never supplies sources, legal advice or vacancies."""

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .llm import LLMError, call_llm


class Intent(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    topic: Literal["work", "education", "housing", "benefits", "documents", "money", "family",
                   "help", "reminders", "plan", "off_topic", "crisis"]
    role: Literal["child", "parent", "candidate"] | None = None
    query: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    region_code: str | None = Field(default=None, pattern=r"^[0-9]{2}00000000000$")
    region_name: str | None = Field(default=None, max_length=100)
    age: int | None = Field(default=None, ge=0, le=100)
    experience: int | None = Field(default=None, ge=0, le=70)
    salary: int | None = Field(default=None, ge=0, le=10000000)
    housing: bool | None = None
    schedule: str | None = Field(default=None, max_length=100)
    family_status: Literal["institution", "graduate", "guardian", "foster", "adopted", "unknown"] | None = None
    study: str | None = Field(default=None, max_length=100)
    skip: bool = False
    skip_fields: list[Literal["experience", "salary"]] = Field(default_factory=list, max_length=2)
    subtopic: Literal["general", "career", "admission", "support", "forms", "steps", "children",
                      "food", "night", "clothes", "hygiene", "queue", "received", "rejection"] = "general"


PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "intent.md"


async def understand(text: str, context: dict) -> Intent:
    raw = await call_llm([
        {"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
        {"role": "user", "content": json.dumps({"context": context, "message": text[:2500]}, ensure_ascii=False)},
    ])
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return Intent.model_validate_json(raw)
    except (ValidationError, ValueError) as error:
        # Field paths only; never log model output or user data.
        if isinstance(error, ValidationError):
            logging.getLogger(__name__).warning("intent_validation fields=%s", [".".join(map(str,e["loc"])) for e in error.errors()])
        raise LLMError("invalid_response") from None
