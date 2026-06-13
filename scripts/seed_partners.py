#!/usr/bin/env python3
"""Idempotent upsert of canonical demo partners (spec §2.3)."""

from __future__ import annotations

import argparse
import json

from scripts.seed_common import CANONICAL_SLUGS, canonical_partner_seeds, seed_partners


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed canonical hub partners and endpoints.")
    parser.add_argument(
        "--print-secrets",
        action="store_true",
        help="Print signing secrets and API keys for newly created partners only.",
    )
    args = parser.parse_args()

    results = seed_partners(canonical_partner_seeds(), print_secrets=args.print_secrets)
    print(f"Seeded {len(results)} canonical partners: {', '.join(CANONICAL_SLUGS)}")
    if args.print_secrets:
        secrets = [row for row in results if row.get("signing_secret") or row.get("api_key")]
        if secrets:
            print(json.dumps(secrets, indent=2))


if __name__ == "__main__":
    main()
