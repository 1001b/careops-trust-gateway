from __future__ import annotations

import argparse
import json

from careops.agent import answer


def main():
    parser = argparse.ArgumentParser(description="CareOps v0.2 naive vs governed RAG agent")
    parser.add_argument("question")
    parser.add_argument("--mode", choices=["naive", "governed"], required=True)
    parser.add_argument("--role", default="analyst", choices=["analyst", "operations", "clinical_admin"])
    parser.add_argument(
        "--synthesize",
        action="store_true",
        help="Force LLM synthesis (Gemini or OpenAI; requires API key in env)",
    )
    parser.add_argument(
        "--no-synthesize",
        action="store_true",
        help="Evidence bundle + deterministic summary only (offline)",
    )
    args = parser.parse_args()
    synthesize = True if args.synthesize else False if args.no_synthesize else None
    result = answer(args.question, mode=args.mode, role=args.role, synthesize=synthesize)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
