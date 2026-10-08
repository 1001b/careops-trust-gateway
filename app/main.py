"""Thin FastAPI surface over governed CareOps core. No domain logic here."""

from __future__ import annotations

import os
import time
import uuid
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from careops.agent import answer as agent_answer
from careops.agent.llm import llm_configured
from careops.gateway import answer as gateway_answer
from careops.storage import using_postgres

MAX_PROMPT = int(os.environ.get("CAREOPS_MAX_PROMPT_CHARS", "800"))
RATE_LIMIT = int(os.environ.get("CAREOPS_RATE_LIMIT_PER_MIN", "20"))
DEMO_ENABLED = os.environ.get("DEMO_ENABLED", "1") != "0"

_rate_lock = Lock()
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)

GUIDED = [
    "Why did in-network appointment availability in Texas decline last week?",
    "What is the current network eligibility policy?",
    "How many bookable Aetna slots were available in Texas last week?",
    "What is the credentialing escalation procedure?",
]


class DemoRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=MAX_PROMPT)
    role: str = Field(default="analyst", pattern="^(analyst|operations|clinical_admin)$")
    mode: str = Field(default="governed", pattern="^(governed|naive|gateway)$")
    synthesize: bool = False


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit(ip: str) -> None:
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets[ip]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        bucket.append(now)


app = FastAPI(
    title="CareOps Trust Gateway",
    description="Governed enterprise data access for AI agents (synthetic demo data only).",
    version="0.2.0",
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "demo_enabled": DEMO_ENABLED,
        "database": "postgres" if using_postgres() else "sqlite",
        "llm_configured": llm_configured(),
        "synthetics_only": True,
    }


@app.get("/api/demo-info")
def demo_info():
    return {
        "project": "careops-trust-gateway",
        "principle": "Models may reason; governed systems remain authoritative.",
        "synthetic_data_only": True,
        "guided_prompts": GUIDED,
        "modes": ["governed", "naive", "gateway"],
        "roles": ["analyst", "operations", "clinical_admin"],
        "llm_configured": llm_configured(),
        "database": "postgres" if using_postgres() else "sqlite",
        "secrets_in_browser": False,
    }


@app.post("/api/demo")
def demo(req: DemoRequest, request: Request):
    if not DEMO_ENABLED:
        raise HTTPException(status_code=503, detail="Demo disabled (DEMO_ENABLED=0)")
    _rate_limit(_client_ip(request))
    request_id = str(uuid.uuid4())
    started = time.time()

    if req.mode == "gateway":
        result = gateway_answer(req.question, role=req.role).as_dict()
        payload = {"status": result["status"], "answer": result["answer"], "evidence": result}
    else:
        payload = agent_answer(
            req.question,
            mode=req.mode,
            role=req.role,
            synthesize=req.synthesize,
        )

    return JSONResponse(
        {
            "request_id": request_id,
            "latency_ms": int((time.time() - started) * 1000),
            "role": req.role,
            "mode": req.mode,
            **payload,
        }
    )


@app.get("/", response_class=HTMLResponse)
def index():
    prompts = "".join(f"<li><code>{p}</code></li>" for p in GUIDED)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>CareOps Trust Gateway</title>
  <style>
    :root {{ --bg:#f6f3ee; --ink:#1c1a17; --accent:#0f5c4c; --line:#d9d2c5; }}
    body {{ margin:0; font-family: "Iowan Old Style", "Palatino Linotype", Palatino, serif;
           background: radial-gradient(circle at top left, #fff9f0, var(--bg) 45%, #e7efe9);
           color: var(--ink); line-height:1.5; }}
    main {{ max-width: 46rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }}
    .banner {{ display:inline-block; border:1px solid var(--line); padding:.2rem .55rem;
               font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.8rem; }}
    h1 {{ font-size: clamp(2rem, 5vw, 3rem); margin: .8rem 0 .4rem; letter-spacing:-.02em; }}
    p {{ max-width: 38rem; }}
    label {{ display:block; margin-top:1rem; font-weight:600; }}
    select, textarea, button {{ width:100%; font: inherit; }}
    textarea {{ min-height: 5rem; padding:.65rem; border:1px solid var(--line); background:#fffdf8; }}
    select {{ padding:.45rem; border:1px solid var(--line); background:#fffdf8; }}
    button {{ margin-top:1rem; background:var(--accent); color:#fff; border:0; padding:.7rem 1rem; cursor:pointer; }}
    pre {{ white-space: pre-wrap; background:#fffdf8; border:1px solid var(--line); padding:1rem; overflow:auto; }}
    ul {{ padding-left: 1.1rem; }}
  </style>
</head>
<body>
<main>
  <div class="banner">Synthetic data only</div>
  <h1>CareOps Trust Gateway</h1>
  <p>Models may reason about enterprise truth. Governed data systems remain authoritative for defining it.</p>
  <p>Guided prompts:</p>
  <ul>{prompts}</ul>
  <label for="role">Role</label>
  <select id="role">
    <option value="analyst">analyst</option>
    <option value="operations">operations</option>
    <option value="clinical_admin">clinical_admin</option>
  </select>
  <label for="mode">Mode</label>
  <select id="mode">
    <option value="governed">governed</option>
    <option value="naive">naive</option>
    <option value="gateway">gateway (v0.1 deterministic)</option>
  </select>
  <label for="q">Question</label>
  <textarea id="q">Why did in-network appointment availability in Texas decline last week?</textarea>
  <label><input type="checkbox" id="synth"/> Synthesize with configured LLM (server-side key only)</label>
  <button id="run" type="button">Run</button>
  <pre id="out">Ready.</pre>
</main>
<script>
const out = document.getElementById('out');
document.getElementById('run').onclick = async () => {{
  out.textContent = 'Running…';
  const body = {{
    question: document.getElementById('q').value,
    role: document.getElementById('role').value,
    mode: document.getElementById('mode').value,
    synthesize: document.getElementById('synth').checked
  }};
  try {{
    const res = await fetch('/api/demo', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body)
    }});
    const data = await res.json();
    out.textContent = JSON.stringify(data, null, 2);
  }} catch (e) {{
    out.textContent = String(e);
  }}
}};
</script>
</body>
</html>"""
