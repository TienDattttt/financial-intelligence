# FinIntel Python SDK (`finintel-sdk`)

Standalone Python client for the FinIntel Open Research API (`read:derived_risk`).

## Dependencies

- `httpx>=0.28`
- `pydantic>=2.10`

## Quickstart

```python
from finintel_sdk import FinIntelClient, StressScenarioInput, verify_webhook_signature

with FinIntelClient(api_key="fi_res_...", base_url="https://api.finintel.vn") as client:
    risk = client.get_company_risk("HPG")
    print(risk.ticker, risk.ml_scores[:1])

    # Subsequent calls automatically send If-None-Match and reuse ETag cache on 304
    risk_cached = client.get_company_risk("HPG")
    assert client.etag_cache_hits >= 1

    screener = client.screen_market(exchange="HOSE", risk_tier="R4", limit=20)
    stress = client.run_stress_test(
        StressScenarioInput(interest_rate_shock_bps=200.0, tickers=["HPG"])
    )
```

## Async Client

```python
from finintel_sdk import AsyncFinIntelClient

async with AsyncFinIntelClient(api_key="fi_res_...") as client:
    risk = await client.get_company_risk("VNM")
```

## Webhook HMAC-SHA256 Verification

```python
is_valid = verify_webhook_signature(
    raw_body=request_body_bytes,
    secret="whsec_...",
    signature_header=request.headers["X-FinIntel-Signature"],
)
```
