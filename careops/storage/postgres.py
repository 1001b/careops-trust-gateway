from __future__ import annotations

import csv

from careops.paths import RAW_DIR, ROOT
from careops.rag.corpus import Chunk, load_chunks
from careops.rag.embed import LocalHashEmbedder
from careops.storage import database_url


def _connect():
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Install postgres extras: pip install -e '.[postgres]'") from exc
    url = database_url()
    if not url:
        raise SystemExit("Set CAREOPS_DATABASE_URL to a postgresql:// URL")
    # psycopg accepts postgresql://; normalize postgres://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return psycopg.connect(url)


def apply_schema(conn) -> None:
    schema = (ROOT / "careops" / "storage" / "schema.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(schema)
    conn.commit()


def _none_if_blank(value: str | None):
    if value is None or value == "":
        return None
    return value


def seed_operational(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("TRUNCATE appointment_slots, provider_networks, providers CASCADE")
        with (RAW_DIR / "providers.csv").open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cur.execute(
                    """
                    INSERT INTO providers (provider_id, provider_name, state, active_from, inactive_from)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        row["provider_id"],
                        row["provider_name"],
                        row["state"],
                        row["active_from"],
                        _none_if_blank(row.get("inactive_from")),
                    ),
                )
        with (RAW_DIR / "provider_networks.csv").open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cur.execute(
                    """
                    INSERT INTO provider_networks
                        (provider_id, payer_network, effective_from, effective_to, status)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        row["provider_id"],
                        row["payer_network"],
                        row["effective_from"],
                        _none_if_blank(row.get("effective_to")),
                        row["status"],
                    ),
                )
        with (RAW_DIR / "appointment_slots.csv").open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cur.execute(
                    """
                    INSERT INTO appointment_slots
                        (slot_id, provider_id, slot_date, payer_network, status)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        row["slot_id"],
                        row["provider_id"],
                        row["slot_date"],
                        row["payer_network"],
                        row["status"],
                    ),
                )
    conn.commit()


def seed_chunks(conn, *, dim: int = 128) -> None:
    embedder = LocalHashEmbedder(dim=dim)
    chunks = load_chunks()
    texts = [f"{c.topic}\n{c.text}" for c in chunks]
    vectors = embedder.embed(texts)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE document_chunks")
        for chunk, vec in zip(chunks, vectors, strict=True):
            cur.execute(
                """
                INSERT INTO document_chunks (
                    chunk_id, doc_id, topic, path, text,
                    effective_from, effective_to, doc_class, roles, authority, embedding
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s::vector
                )
                """,
                (
                    chunk.chunk_id,
                    chunk.doc_id,
                    chunk.topic,
                    chunk.path,
                    chunk.text,
                    chunk.effective_from,
                    chunk.effective_to,
                    chunk.doc_class,
                    list(chunk.roles),
                    chunk.authority,
                    str(vec),
                ),
            )
    conn.commit()


def load_chunks_with_embeddings() -> tuple[list[Chunk], list[list[float]]]:
    """Load chunk rows + stored pgvector embeddings from Postgres."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_id, doc_id, topic, path, text,
                       effective_from::text, effective_to::text,
                       doc_class, roles, authority, embedding::text
                FROM document_chunks
                ORDER BY chunk_id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    chunks: list[Chunk] = []
    vectors: list[list[float]] = []
    for row in rows:
        roles = tuple(row[8] or [])
        chunks.append(
            Chunk(
                chunk_id=row[0],
                doc_id=row[1],
                topic=row[2],
                path=row[3],
                text=row[4],
                effective_from=row[5],
                effective_to=row[6],
                doc_class=row[7],
                roles=roles,
                authority=int(row[9]),
            )
        )
        emb = row[10]
        # pgvector text form: [0.1,0.2,...]
        if isinstance(emb, str):
            inner = emb.strip()[1:-1]
            vectors.append([float(x) for x in inner.split(",") if x.strip()])
        else:
            vectors.append([float(x) for x in emb])
    return chunks, vectors


def bootstrap_postgres() -> str:
    conn = _connect()
    try:
        apply_schema(conn)
        seed_operational(conn)
        seed_chunks(conn)
    finally:
        conn.close()
    return "postgres bootstrap complete"


if __name__ == "__main__":
    print(bootstrap_postgres())
