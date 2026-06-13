#!/usr/bin/env python3
"""Idempotent prod-like partner catalog (canonical four + extras)."""

from __future__ import annotations

import argparse
import json

from scripts.seed_common import (
    CANONICAL_SLUGS,
    canonical_partner_seeds,
    prod_like_extra_seeds,
    seed_partners,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed prod-like partner catalog.")
    parser.add_argument(
        "--print-secrets",
        action="store_true",
        help="Print signing secrets and API keys for newly created partners only.",
    )
    args = parser.parse_args()

    seeds = canonical_partner_seeds() + prod_like_extra_seeds()
    results = seed_partners(seeds, print_secrets=args.print_secrets)
    print(
        f"Seeded {len(results)} prod-like partners "
        f"(canonical: {', '.join(CANONICAL_SLUGS)}; extras: {len(prod_like_extra_seeds())})"
    )
    if args.print_secrets:
        secrets = [row for row in results if row.get("signing_secret") or row.get("api_key")]
        if secrets:
            print(json.dumps(secrets, indent=2))


if __name__ == "__main__":
    main()
