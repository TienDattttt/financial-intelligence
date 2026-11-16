from types import SimpleNamespace

from pytest import MonkeyPatch

from scripts import spike_vnstock6 as spike


def test_inventory_distinguishes_missing_fields_from_explicit_null() -> None:
    fields = spike.inventory_fields(
        [
            {"year": 2025, "metric": 120, "nullable": None},
            {"year": 2024, "metric": None},
        ]
    )
    assert fields["metric"] == {"types": {"integer": 1, "null": 1}, "missing": 0}
    assert fields["nullable"] == {"types": {"null": 1}, "missing": 1}


def test_flow_comparison_distinguishes_discrete_ytd_and_mismatches() -> None:
    field = "metric"
    values = {2022: (1, 2, 3, 4), 2023: (1, 3, 6, 10), 2024: (1, 2, 3, 5)}
    quarters = [
        {"year": year, "quarter": q, field: value}
        for year, four in values.items()
        for q, value in enumerate(four, start=1)
    ]
    years = [{"year": year, field: 10} for year in values]
    result = spike.compare_flow(quarters, years, field, tolerance=0)
    assert result["counts"] == {
        "discrete_only": 1,
        "ytd_only": 1,
        "ambiguous": 0,
        "neither": 1,
        "missing": 0,
    }
    assert result["by_year"][-1]["sum_minus_annual_vnd"] == 1


def test_balance_identity_records_missing_and_violations() -> None:
    assets, liabilities, equity = spike.BALANCE_FIELDS.values()
    result = spike.check_balance(
        {
            "quarter": [
                {"year": 2025, "quarter": 1, assets: 101, liabilities: 60, equity: 40},
                {"year": 2025, "quarter": 2, assets: 150, liabilities: 60, equity: 40},
                {
                    "year": 2025,
                    "quarter": 3,
                    assets: 150,
                    liabilities: None,
                    equity: 40,
                },
            ],
            "year": [],
        }
    )
    assert result["counts"] == {"pass": 1, "fail": 1, "missing": 1}
    assert result["max_absolute_residual_vnd"] == 50
    assert result["by_period"][-1]["residual_vnd"] is None


def test_benchmark_fetches_150_distinct_first_pages(monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[str, str, str]] = []
    traffic = spike.HttpObservations()

    def plan(
        name: str, symbol: str, *, statement: str, period: str
    ) -> list[SimpleNamespace]:
        assert name == "financials"
        return [
            SimpleNamespace(
                operation="financial-statement/statement",
                params={
                    "symbol": symbol,
                    "type": statement,
                    "period": period,
                    "page": 1,
                },
            ),
        ]

    def raw(
        operation: str, *, only_page: int, **kwargs: object
    ) -> list[SimpleNamespace]:
        assert operation == "financial-statement/statement"
        assert only_page == 1
        assert kwargs["limit"] == 5
        calls.append(
            (str(kwargs["symbol"]), str(kwargs["type"]), str(kwargs["period"]))
        )
        traffic.on_response(SimpleNamespace(status_code=200, headers={}))
        return [SimpleNamespace(http_status=200, body={"data": [{"year": 2025}]})]

    monkeypatch.setattr(spike, "sleep", lambda _: None)
    result = spike.benchmark(SimpleNamespace(plan=plan, raw=raw), traffic)
    assert len(calls) == len(set(calls)) == 150
    assert result["successful_calls"] == 150
    assert result["http_status_distribution"] == {"200": 150}
    assert result["http_requests_observed"] == 150


def test_benchmark_keeps_original_429_even_if_sdk_retry_succeeds() -> None:
    traffic = spike.HttpObservations()

    def raw(*args: object, **kwargs: object) -> list[SimpleNamespace]:
        traffic.on_response(
            SimpleNamespace(
                status_code=429,
                headers={
                    "retry-after": "3",
                    "set-cookie": "private",
                },
            )
        )
        traffic.on_response(SimpleNamespace(status_code=200, headers={}))
        return [SimpleNamespace(http_status=200, body={"data": [{"year": 2025}]})]

    source = SimpleNamespace(
        plan=lambda *a, **kw: [SimpleNamespace(operation="financials", params={})],
        raw=raw,
    )
    result = spike.benchmark(source, traffic)
    assert result["attempted_calls"] == 1
    assert result["stop_reason"] == "rate_limit"
    assert result["http_status_distribution"] == {"429": 1, "200": 1}
    assert result["rate_limit_headers_observed"] == {"retry-after": ["3"]}
