from __future__ import annotations

import argparse
import json
from .gateway import answer


def main():
    parser = argparse.ArgumentParser(description="CareOps Trust Gateway demo")
    parser.add_argument("question")
    parser.add_argument("--role", default="analyst", choices=["analyst", "operations", "clinical_admin"])
    args = parser.parse_args()
    response = answer(args.question, role=args.role)
    print(json.dumps(response.as_dict(), indent=2))


if __name__ == "__main__":
    main()
