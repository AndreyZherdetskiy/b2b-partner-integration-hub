#!/usr/bin/env python3
"""Export OpenAPI snapshot to docs/openapi/."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.main import create_app


def main() -> None:
    spec = create_app().openapi()
    out_dir = Path("docs/openapi")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "openapi.json").write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / "openapi.yaml").write_text(
        yaml.dump(spec, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"Wrote {out_dir / 'openapi.json'} and {out_dir / 'openapi.yaml'}")


if __name__ == "__main__":
    main()
