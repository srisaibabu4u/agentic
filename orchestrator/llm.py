"""Claude Messages API client for live mode.

run_agent is the agentic loop every live agent and the live master use. The
agent gets its system prompt from prompts/<agent>.md, a task brief, its
read-only tools and a submit_result tool. It works until it submits a result
that passes its checks, or until its turn budget runs out (then the result is
FAILED and the master's retry guardrail, G4, or the playbook fallback takes
over). Every tool call is kept in result["agent_trace"] for the audit log.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Callable

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

AGENTIC = """

## How you work
You work autonomously with the tools you are given. Nobody answers questions mid-task.
1. Plan: read the task brief and decide what you need to look up.
2. Gather: call tools to get the evidence. Never state a value you have not seen in a tool result.
3. Check: use the checking tools (freshness, rating, citations) before you rely on a conclusion.
4. Submit: call submit_result once, with "result" set to the JSON object described under Output.
   If submit_result returns errors, fix every one and call it again.
Tools only read data; you cannot change the case. Keep going until submit_result accepts your result."""

SUBMIT = {"name": "submit_result",
          "description": "Submit your final result. It is checked against the evidence; on errors, fix them and resubmit.",
          "input_schema": {"type": "object", "required": ["result"], "properties": {
              "result": {"type": "object", "description": "The JSON object described under Output in your instructions."},
              "self_check": {"type": "string", "description": "One or two sentences: how you verified the result."}}}}


def load_system_prompt(name: str, **vars) -> str:
    text = (PROMPTS / name).read_text(encoding="utf-8")
    for k, v in vars.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text.strip()


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def _short(obj, n: int = 300) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return s if len(s) <= n else s[:n] + "…"


class LLM:
    def __init__(self, model: str | None = None, max_tokens: int = 16000, max_turns: int | None = None,
                 effort: str | None = None, client=None):
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise SystemExit("Live mode needs the SDK: pip install anthropic") from e
            client = anthropic.Anthropic()  # credentials from ANTHROPIC_API_KEY or an `ant auth login` profile
        self.client = client
        self.model = model or os.environ.get("KYC_MODEL", "claude-sonnet-5-5")
        self.effort = effort or os.environ.get("KYC_EFFORT", "medium")
        self.max_turns = max_turns or int(os.environ.get("KYC_MAX_TURNS", "20"))
        self.max_tokens = max_tokens
        self.calls = 0

    def _create(self, **kw):
        self.calls += 1
        # fallbacks="default": if a safety classifier declines, the API reruns the request on a suitable model
        return self.client.beta.messages.create(model=self.model, max_tokens=self.max_tokens,
                                                output_config={"effort": self.effort},
                                                betas=[FALLBACK_BETA], fallbacks="default", **kw)

    # ------------------------------------------------------------ agentic loop
    def run_agent(self, prompt_file: str, brief: dict, tools: list, validate: Callable[[dict], list[str]],
                  **vars) -> dict:
        system = [{"type": "text", "text": load_system_prompt(prompt_file, **vars) + AGENTIC,
                   "cache_control": {"type": "ephemeral"}}]
        specs = [t.spec() for t in tools] + [SUBMIT]
        by_name = {t.name: t for t in tools}
        messages = [{"role": "user", "content": "<task_brief>\n" + json.dumps(brief, indent=2, default=str) +
                     "\n</task_brief>\nWork through the task with your tools, then call submit_result."}]
        trace = {"model": self.model, "effort": self.effort, "turns": 0, "steps": [], "rejected_submissions": 0,
                 "input_tokens": 0, "output_tokens": 0}

        for turn in range(1, self.max_turns + 1):
            resp = self._create(system=system, tools=specs, messages=messages)
            trace["turns"] = turn
            trace["input_tokens"] += getattr(resp.usage, "input_tokens", 0) or 0
            trace["output_tokens"] += getattr(resp.usage, "output_tokens", 0) or 0
            if resp.stop_reason == "refusal":
                return self._failed("model declined the task", trace)
            messages.append({"role": "assistant", "content": resp.content})
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:  # tool_choice "auto" does not guarantee a call: steer it back to the contract
                messages.append({"role": "user", "content": "You have not submitted. Continue with your tools, "
                                                            "then call submit_result with your result."})
                continue

            results, accepted = [], None
            for u in uses:
                content, is_error = self._run_tool(u, by_name, validate, trace, turn)
                if u.name == "submit_result" and not is_error:
                    accepted = u.input
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": content, "is_error": is_error})
            if accepted is not None:
                trace["self_check"] = accepted.get("self_check", "")
                return {**self._result(accepted), "agent_trace": trace}
            messages.append({"role": "user", "content": results})  # all results of one turn in one message

        return self._failed(f"no accepted result within {self.max_turns} turns", trace)

    def _run_tool(self, u, by_name: dict, validate, trace: dict, turn: int) -> tuple[str, bool]:
        step = {"turn": turn, "tool": u.name, "input": _short(u.input, 200)}
        trace["steps"].append(step)
        try:
            if u.name == "submit_result":
                errs = validate(self._result(u.input))
                if errs:
                    trace["rejected_submissions"] += 1
                    step.update(ok=False, output=_short(errs))
                    return "Result rejected. Fix these and call submit_result again:\n- " + "\n- ".join(errs), True
                step.update(ok=True, output="accepted")
                return "Accepted.", False
            if u.name not in by_name:
                raise ValueError(f"unknown tool {u.name}")
            out = json.dumps(by_name[u.name].fn(**(u.input or {})), default=str)
            step.update(ok=True, output=_short(out, 120))
            return out, False
        except Exception as e:  # a tool error goes back to the agent, which can try something else
            step.update(ok=False, output=_short(f"{type(e).__name__}: {e}"))
            return f"{type(e).__name__}: {e}", True

    @staticmethod
    def _result(submitted: dict) -> dict:
        res = submitted.get("result", {})
        return parse_json(res) if isinstance(res, str) else res  # some turns send the object as a JSON string

    @staticmethod
    def _failed(error: str, trace: dict) -> dict:
        return {"status": "FAILED", "error": error, "agent_trace": trace}
