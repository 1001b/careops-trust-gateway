from __future__ import annotations

import json
import os

from careops.metrics import get_metric

SYSTEM_PROMPT = (
    "You are an operations analyst. You may reason over the provided evidence bundle only. "
    "You must not invent authoritative metrics, eligibility statuses, or policy text. "
    "If evidence.status is insufficient_evidence, or required facts are missing, say so explicitly. "
    "Cite sources by path/doc_id. Quantitative values may only come from evidence.metric "
    "or successful get_metric tool results. "
    "Keep the final answer concise (under 200 words). "
    "If evidence.metric is already populated, use those values directly and do not call tools unless needed."
)

GET_METRIC_SCHEMA = {
    "type": "object",
    "properties": {
        "metric": {"type": "string"},
        "state": {"type": "string"},
        "payer_network": {"type": "string"},
        "period": {"type": "string", "enum": ["last_week", "prior_week"]},
    },
    "required": ["metric", "state", "payer_network", "period"],
}


def llm_configured() -> bool:
    provider = (os.environ.get("CAREOPS_LLM_PROVIDER") or "auto").lower()
    has_gemini = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    if provider == "gemini":
        return has_gemini
    if provider == "openai":
        return has_openai
    return has_gemini or has_openai


def resolve_llm_provider() -> str | None:
    provider = (os.environ.get("CAREOPS_LLM_PROVIDER") or "auto").lower()
    has_gemini = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    if provider == "gemini":
        return "gemini" if has_gemini else None
    if provider == "openai":
        return "openai" if has_openai else None
    if has_gemini:
        return "gemini"
    if has_openai:
        return "openai"
    return None


def _run_get_metric(args: dict) -> dict:
    return get_metric(
        args.get("metric", "available_appointments"),
        state=args.get("state", "TX"),
        payer_network=args.get("payer_network", "Aetna"),
        period=args.get("period", "last_week"),
    ).as_dict()


def _user_prompt(question: str, evidence_dict: dict) -> str:
    return (
        f"Question: {question}\n\n"
        f"Mode: {evidence_dict.get('mode')}\n"
        f"Evidence JSON:\n{json.dumps(evidence_dict)}"
    )


def synthesize_openai(question: str, evidence_dict: dict) -> dict:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("Install RAG extras: pip install -e '.[rag]'") from exc

    if not os.environ.get("OPENAI_API_KEY"):
        return {
            "status": "model_unavailable",
            "answer": None,
            "reason": "OPENAI_API_KEY not set",
            "provider": "openai",
            "evidence": evidence_dict,
        }

    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    client = OpenAI()
    tools = []
    if evidence_dict.get("mode") == "governed":
        tools = [
            {
                "type": "function",
                "name": "get_metric",
                "description": "Return an authoritative governed metric. Demo supports available_appointments.",
                "parameters": {**GET_METRIC_SCHEMA, "additionalProperties": False},
            }
        ]

    input_messages: list[dict] = [
        {"role": "developer", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(question, evidence_dict)},
    ]
    max_out = int(os.environ.get("CAREOPS_MAX_OUTPUT_TOKENS", "2048"))

    for _ in range(3):
        kwargs: dict = {"model": model, "input": input_messages, "max_output_tokens": max_out}
        if tools:
            kwargs["tools"] = tools
        try:
            response = client.responses.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "model_unavailable",
                "answer": None,
                "reason": f"OpenAI Responses API failed: {exc}",
                "provider": "openai",
                "evidence": evidence_dict,
            }

        tool_calls = []
        text_bits = []
        for item in response.output:
            item_type = getattr(item, "type", None)
            if item_type == "function_call":
                tool_calls.append(item)
            elif item_type == "message":
                for part in getattr(item, "content", []) or []:
                    if getattr(part, "type", None) == "output_text":
                        text_bits.append(part.text)

        if not tool_calls:
            answer_text = getattr(response, "output_text", None) or "\n".join(text_bits)
            return {
                "status": "ok" if evidence_dict.get("status") == "ok" else evidence_dict.get("status"),
                "answer": answer_text,
                "model": model,
                "provider": "openai",
                "evidence": evidence_dict,
            }

        for call in tool_calls:
            input_messages.append(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
            )
            if call.name == "get_metric":
                result = _run_get_metric(json.loads(call.arguments))
                output = json.dumps(result)
            else:
                output = '{"error":"tool_not_allowed"}'
            input_messages.append(
                {"type": "function_call_output", "call_id": call.call_id, "output": output}
            )

    return {
        "status": "ok",
        "answer": "Tool loop exceeded bound without final answer.",
        "model": model,
        "provider": "openai",
        "evidence": evidence_dict,
    }


def synthesize_gemini(question: str, evidence_dict: dict) -> dict:
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise SystemExit("Install RAG extras: pip install -e '.[rag]'") from exc

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return {
            "status": "model_unavailable",
            "answer": None,
            "reason": "GEMINI_API_KEY (or GOOGLE_API_KEY) not set",
            "provider": "gemini",
            "evidence": evidence_dict,
        }

    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
    client = genai.Client(api_key=api_key)
    # Newer Gemini models may spend part of the output budget on internal reasoning.
    max_out = int(os.environ.get("CAREOPS_MAX_OUTPUT_TOKENS", "2048"))

    # Metric is already in the evidence bundle for governed mixed questions; only expose
    # the tool when metric evidence is missing to avoid wasted tool-loop tokens.
    tools = None
    if evidence_dict.get("mode") == "governed" and not evidence_dict.get("metric"):
        tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="get_metric",
                        description=(
                            "Return an authoritative governed metric. "
                            "Demo supports available_appointments."
                        ),
                        parameters=GET_METRIC_SCHEMA,
                    )
                ]
            )
        ]

    contents: list = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=_user_prompt(question, evidence_dict))],
        )
    ]
    config_kwargs: dict = {
        "system_instruction": SYSTEM_PROMPT,
        "max_output_tokens": max_out,
        "temperature": 0.2,
    }
    if tools:
        config_kwargs["tools"] = tools
        # Manual tool loop below; disable SDK automatic function calling to avoid AFC warnings.
        try:
            config_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(
                disable=True
            )
        except Exception:  # noqa: BLE001 - older SDKs may lack this type
            pass
    config = types.GenerateContentConfig(**config_kwargs)

    for _ in range(3):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "model_unavailable",
                "answer": None,
                "reason": f"Gemini API failed: {exc}",
                "provider": "gemini",
                "evidence": evidence_dict,
            }

        candidate = (response.candidates or [None])[0]
        if candidate is None or candidate.content is None:
            return {
                "status": "model_unavailable",
                "answer": None,
                "reason": "Gemini returned no candidates",
                "provider": "gemini",
                "model": model,
                "evidence": evidence_dict,
            }

        parts = candidate.content.parts or []
        function_calls = [p for p in parts if getattr(p, "function_call", None)]
        text_bits = [p.text for p in parts if getattr(p, "text", None)]
        finish = str(getattr(candidate, "finish_reason", "") or "")

        if not function_calls:
            answer_text = (response.text if getattr(response, "text", None) else None) or "\n".join(
                text_bits
            )
            result = {
                "status": "ok" if evidence_dict.get("status") == "ok" else evidence_dict.get("status"),
                "answer": answer_text,
                "model": model,
                "provider": "gemini",
                "evidence": evidence_dict,
            }
            if "MAX_TOKENS" in finish.upper():
                result["warning"] = "response_truncated_by_max_output_tokens"
            return result

        # Append model turn, then tool responses
        contents.append(candidate.content)
        response_parts = []
        for part in function_calls:
            fc = part.function_call
            name = fc.name
            args = dict(fc.args or {})
            if name == "get_metric":
                result = _run_get_metric(args)
            else:
                result = {"error": "tool_not_allowed"}
            response_parts.append(
                types.Part.from_function_response(name=name, response=result)
            )
        contents.append(types.Content(role="user", parts=response_parts))

    return {
        "status": "ok",
        "answer": "Tool loop exceeded bound without final answer.",
        "model": model,
        "provider": "gemini",
        "evidence": evidence_dict,
    }


def synthesize_with_model(question: str, evidence_dict: dict) -> dict:
    provider = resolve_llm_provider()
    if provider is None:
        return {
            "status": "model_unavailable",
            "answer": None,
            "reason": (
                "No LLM API key configured. Set GEMINI_API_KEY or OPENAI_API_KEY "
                "(and optionally CAREOPS_LLM_PROVIDER=gemini|openai)."
            ),
            "evidence": evidence_dict,
        }
    if provider == "gemini":
        return synthesize_gemini(question, evidence_dict)
    return synthesize_openai(question, evidence_dict)
