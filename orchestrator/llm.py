"""Thin wrapper around the Claude Messages API for live mode.

Loads the system prompt from prompts/<agent>.md, sends the task payload as the
user message, and returns parsed, key-checked JSON. One repair attempt is made
if the reply is not valid JSON; after that the agent result is FAILED and the
master's retry guardrail (G4) takes over.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
SPLIT = "<!-- USER MESSAGE TEMPLATE -->"


def load_system_prompt(name: str, **vars) -> str:
    text = (PROMPTS / name).read_text().split(SPLIT)[0]
    for k, v in vars.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text.strip()


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


class LLM:
    def __init__(self, model: str | None = None, max_tokens: int = 4000):
        try:
            import anthropic
        except ImportError as e:
            raise SystemExit("Live mode needs the SDK: pip install anthropic") from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("Live mode needs ANTHROPIC_API_KEY in the environment.")
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("KYC_MODEL", "claude-sonnet-5-5")
        self.max_tokens = max_tokens
        self.calls = 0

    def call_json(self, prompt_file: str, payload: dict, required_keys: tuple = (), **vars) -> dict:
        system = load_system_prompt(prompt_file, **vars)
        messages = [{"role": "user", "content":
                     "<task_payload>\n" + json.dumps(payload, indent=2, default=str) +
                     "\n</task_payload>\nReturn JSON only, following the output format in your instructions."}]
        last_err = ""
        for attempt in range(2):
            self.calls += 1
            resp = self.client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                               temperature=0, system=system, messages=messages)
            text = "".join(b.text for b in resp.content if b.type == "text")
            try:
                out = parse_json(text)
                missing = [k for k in required_keys if k not in out]
                if missing:
                    raise ValueError(f"missing keys {missing}")
                return out
            except Exception as e:  # one repair attempt with the error shown to the model
                last_err = str(e)
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"Your reply was not valid: {last_err}. Return the corrected JSON only."}]
        return {"status": "FAILED", "error": f"invalid JSON after repair: {last_err}"}
