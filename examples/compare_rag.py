"""Fair comparison: same embedder + corpus; naive vs governed retrieval (+ optional GPT).

v0.1 examples/naive_rag.py remains the frozen lexical unsafe baseline.
This script is the v0.2 experiment.
"""
from __future__ import annotations

import json
import sys

from careops.agent import answer
from careops.rag.embed import LocalHashEmbedder


QUESTION = (
    "Why did in-network appointment availability in Texas decline last week? "
    "What is the current network eligibility policy?"
)


def main(question: str):
    embedder = LocalHashEmbedder()  # identical embedder for both paths
    naive = answer(question, mode="naive", role="analyst", synthesize=False, embedder=embedder)
    governed = answer(question, mode="governed", role="analyst", synthesize=False, embedder=embedder)
    print(json.dumps({"question": question, "naive": naive, "governed": governed}, indent=2))


if __name__ == "__main__":
    main(" ".join(sys.argv[1:]) or QUESTION)
