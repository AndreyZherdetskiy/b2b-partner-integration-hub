# OpenAPI snapshot

The **live** HTTP contract is FastAPI:

- http://localhost:8000/docs
- http://localhost:8000/redoc
- http://localhost:8000/openapi.json

Regenerate the committed snapshot:

```bash
make export-openapi
```

Output: `docs/openapi/openapi.yaml` (and JSON if the exporter writes both).

This directory is not an endpoint catalog. Do not add markdown tables of routes.
