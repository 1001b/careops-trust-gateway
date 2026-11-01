from __future__ import annotations

import argparse
import json

from .conformance import project_gateway_payload
from .gateway import answer


def respond(question: str, role: str = "analyst") -> dict:
    payload = answer(question, role=role).as_dict()
    payload["governed"] = project_gateway_payload(payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description="CareOps Trust Gateway demo")
    parser.add_argument("question")
    parser.add_argument("--role", default="analyst", choices=["analyst", "operations", "clinical_admin"])
    args = parser.parse_args()
    print(json.dumps(respond(args.question, args.role), indent=2))


if __name__ == "__main__":
    main()
