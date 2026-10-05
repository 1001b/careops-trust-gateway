from __future__ import annotations

from dataclasses import dataclass
import json
import re

from careops.paths import ROOT, KNOWLEDGE_DIR

HEADING_RE = re.compile(r"(?m)^(#{1,3}\s+.+)$")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    topic: str
    path: str
    text: str
    effective_from: str
    effective_to: str | None
    doc_class: str
    roles: tuple[str, ...]
    authority: int


def _split_markdown(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = HEADING_RE.split(text)
    if len(parts) == 1:
        return [text]
    chunks: list[str] = []
    # parts alternates: preamble, heading, body, heading, body, ...
    preamble = parts[0].strip()
    if preamble:
        chunks.append(preamble)
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        block = f"{heading}\n\n{body}".strip() if body else heading
        if block:
            chunks.append(block)
    return chunks or [text]


def load_chunks() -> list[Chunk]:
    index = json.loads((KNOWLEDGE_DIR / "index.json").read_text(encoding="utf-8"))
    out: list[Chunk] = []
    for item in index:
        content = (ROOT / item["path"]).read_text(encoding="utf-8")
        pieces = _split_markdown(content)
        for i, piece in enumerate(pieces):
            out.append(
                Chunk(
                    chunk_id=f"{item['id']}::{i}",
                    doc_id=item["id"],
                    topic=item["topic"],
                    path=item["path"],
                    text=piece.strip(),
                    effective_from=item["effective_from"],
                    effective_to=item.get("effective_to"),
                    doc_class=item["class"],
                    roles=tuple(item.get("roles", [])),
                    authority=int(item.get("authority", 0)),
                )
            )
    return out


def chunk_to_dict(chunk: Chunk) -> dict:
    return {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "topic": chunk.topic,
        "path": chunk.path,
        "text": chunk.text,
        "effective_from": chunk.effective_from,
        "effective_to": chunk.effective_to,
        "class": chunk.doc_class,
        "roles": list(chunk.roles),
        "authority": chunk.authority,
    }
