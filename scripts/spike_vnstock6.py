"""Survey FHSC's raw financial statements through vnstock-oapi.

The full run captures FPT history and benchmarks 150 single-page calls.
Use --reuse-history to benchmark without downloading the captured history again.
"""

from __future__ import annotations

import argparse
import getpass
import inspect
import json
import os
from collections import Counter
from datetime import UTC, datetime
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from statistics import median, quantiles
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data/metadata/spike_report.json"
HISTORY_PATH = ROOT / "data/metadata/fhsc_spike_fpt"
REFERENCE_PATH = ROOT / "tests/fixtures/raw_samples/fpt_2025_spike_reference.json"
SDK_VERSION = "0.1.2"
SOURCE = "fhsc"
STATEMENTS = ("balance-sheet", "income-statement", "cash-flow")
PERIODS = ("quarter", "year")
SYMBOLS = tuple(
    "FPT VNM HPG MWG PNJ GAS PLX VJC HVN VRE VHM VIC KDH NLG GMD HAH VSC "
    "DGC DCM DPM REE POW SAB FRT DGW".split()
)
PAGE_SIZE = 5
INTERVAL_SECONDS = 2.0
TOLERANCE_VND = 1
BALANCE_FIELDS = {
    "total_assets": "tongcongtaisan",
    "total_liabilities": "nophaitra",
    "total_equity": "vonchusohuu_tong",
}
FLOW_FIELDS = {
    "net_revenue": ("income-statement", "doanhthuthuanvebanhangvacungcapdichvu"),
    "net_income": ("income-statement", "loinhuansauthuethunhapdoanhnghiep"),
    "operating_cash_flow": ("cash-flow", "luuchuyentienthuantuhoatdongkinhdoanh"),
}
DOCS_URL = (
    "https://developers.fhsc.com.vn/api-reference/phan-tich-co-ban/bao-cao-tai-chinh"
)
RATE_DOCS_URL = "https://developers.fhsc.com.vn/rate-limits"
HARD_STOP_STATUSES = {401, 403, 429}

Json = dict[str, Any]


def write_report(path: Path, report: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        temporary.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


def read_json(path: Path) -> Json:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return dict(value)


def error_details(error: Exception) -> Json:
    status = getattr(error, "http_status", None)
    return {
        "error_type": type(error).__name__,
        "http_status": status if isinstance(status, int) else None,
    }


def fetch_page(
    source: Any, symbol: str, statement: str, period: str, page: int, **overrides: Any
) -> tuple[Any, Json]:
    (plan,) = source.plan("financials", symbol, statement=statement, period=period)
    query = {**plan.params, "limit": PAGE_SIZE, "page": page, **overrides}
    (envelope,) = source.raw(plan.operation, only_page=page, **query)
    return envelope, query


def body_rows(body: object) -> list[Json]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("Expected FHSC response with a data array")
    rows: list[Json] = body["data"]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Expected an object for every FHSC period")
    return rows


def period_key(row: Json, period: str) -> tuple[int, int | None]:
    year, quarter = row.get("year"), row.get("quarter")
    if type(year) is not int or year < 1:
        raise ValueError("Invalid FHSC reporting year")
    if period == "quarter" and (type(quarter) is not int or not 1 <= quarter <= 4):
        raise ValueError("Invalid FHSC reporting quarter")
    if period == "year" and quarter is not None:
        raise ValueError("Unexpected quarter in FHSC annual response")
    return year, quarter


def capture_history(source: Any, directory: Path, source_factory: Any) -> Json:
    """Archive decoded FHSC bodies unchanged in structure, one file per page."""
    directory.mkdir(parents=True, exist_ok=False)
    signatures = {
        obj.__qualname__: str(inspect.signature(obj))
        for obj in (
            source_factory,
            source.plan,
            source.raw,
            source.spec.extras["financials"],
        )
    }
    inventory: Json = {
        "checked_at": datetime.now(UTC).isoformat(),
        "signatures": signatures,
        "client_rate_per_second": source.spec.rate_per_second,
        "requests": [],
        "query_probes": [],
    }
    page_limit = source.spec.ops["financial-statement/statement"].max_pages
    try:
        for statement in STATEMENTS:
            for period in PERIODS:
                seen: set[tuple[int, int | None]] = set()
                for page in range(1, page_limit + 1):
                    envelope, query = fetch_page(source, "FPT", statement, period, page)
                    current = body_rows(envelope.body)
                    path = directory / f"{statement}-{period}-{page:03}.json"
                    write_report(path, envelope.body)
                    inventory["requests"].append(
                        {
                            "statement": statement,
                            "period": period,
                            "page": page,
                            "records": len(current),
                            "wire_query": query,
                            "http_status": envelope.http_status,
                            "body_sha256": envelope.body_sha256,
                            "body_path": str(path),
                        }
                    )
                    keys = {period_key(row, period) for row in current}
                    if len(keys) != len(current) or seen.intersection(keys):
                        raise ValueError("Duplicate period in FHSC pagination")
                    seen.update(keys)
                    if not current:
                        break
                else:
                    raise ValueError("FHSC history reached the client's page cap")
        baseline = body_rows(read_json(directory / "income-statement-quarter-001.json"))
        for param, period, page, overrides in (
            ("page_size", "quarter", 1, {"page_size": 50}),
            ("offset", "quarter", 1, {"offset": 5}),
            ("statement_scope", "quarter", 1, {"statement_scope": "separate"}),
            ("limit", "quarter", 1, {"limit": 20}),
            ("limit", "quarter", 2, {"limit": 3}),
            ("year", "quarter", 1, {"year": 2025}),
            ("year", "year", 1, {"year": 2025}),
        ):
            result: Json = {
                "parameter": param,
                "test": overrides,
                "period": period,
                "page": page,
            }
            inventory["query_probes"].append(result)
            try:
                envelope, query = fetch_page(
                    source, "FPT", "income-statement", period, page, **overrides
                )
                rows = body_rows(envelope.body)
                result.update(
                    test=query,
                    http_status=envelope.http_status,
                    records=len(rows),
                    periods=[period_key(row, period) for row in rows],
                    same_records_as_baseline=rows == baseline,
                )
            except Exception as error:
                result.update(error_details(error))
                if param != "limit" or result["http_status"] != 400:
                    raise
    finally:
        write_report(directory / "inventory.json", inventory)
    return inventory


def inventory_fields(objects: list[Json]) -> Json:
    """Inventory all field names, types, presence and explicit nulls."""
    js_types = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
        type(None): "null",
    }
    counts: dict[str, Counter[str]] = {}
    for obj in objects:
        for key, value in obj.items():
            counts.setdefault(key, Counter())[js_types[type(value)]] += 1
    return {
        key: {
            "types": dict(sorted(types.items())),
            "missing": len(objects) - types.total(),
        }
        for key, types in sorted(counts.items())
    }


def analyse_history(
    directory: Path, inventory: Json
) -> tuple[Json, dict[str, dict[str, list[Json]]]]:
    history: dict[str, dict[str, list[Json]]] = {}
    schema: Json = {}
    coverage: Json = {}
    for statement in STATEMENTS:
        history[statement], schema[statement], coverage[statement] = {}, {}, {}
        for period in PERIODS:
            calls = [
                call
                for call in inventory["requests"]
                if (call["statement"], call["period"]) == (statement, period)
            ]
            bodies = [
                read_json(directory / Path(call["body_path"]).name) for call in calls
            ]
            rows = [row for body in bodies for row in body_rows(body)]
            keys = [period_key(row, period) for row in rows]
            verified = (
                bool(calls)
                and calls[-1]["records"] == 0
                and [call["page"] for call in calls] == list(range(1, len(calls) + 1))
                and all(call["http_status"] == 200 for call in calls)
                and len(keys) == len(set(keys))
                and keys == sorted(keys, reverse=True)
                and [len(body_rows(body)) for body in bodies]
                == [call["records"] for call in calls]
            )
            history[statement][period] = rows
            schema[statement][period] = {
                "body_fields": inventory_fields(bodies),
                "data_record_fields": inventory_fields(rows),
                "record_count": len(rows),
            }
            coverage[statement][period] = {
                "unique_periods": len(set(keys)),
                "latest": keys[0] if keys else None,
                "earliest": keys[-1] if keys else None,
                "page_count": len(calls),
                "records_by_page": [call["records"] for call in calls],
                "verified_to_empty_page": verified,
                "meets_history_minimum": verified
                and len(keys) >= (16 if period == "quarter" else 4),
            }
    findings = {
        "schema_metadata": schema,
        "history": {
            "symbol": "FPT",
            "capture_directory": directory.as_posix(),
            "coverage": coverage,
            "complete": all(
                x["meets_history_minimum"]
                for by_period in coverage.values()
                for x in by_period.values()
            ),
            "archive_format": "Decoded FHSC JSON body, reserialized to UTF-8. The inventory retains the SDK body digest.",
        },
        "pagination": {
            "api_docs": DOCS_URL,
            "operation": "GET /market/v2/financial-statement/statement",
            "source_signatures": {
                "Source.__init__" if name == "Source" else name: signature
                for name, signature in inventory["signatures"].items()
            },
            "sdk_configured_requests_per_second": inventory["client_rate_per_second"],
            "wire_parameters": {
                "symbol": {"required": True, "response_field": False},
                "type": list(STATEMENTS),
                "period": {"quarter": "quarterly", "year": "annual"},
                "page": {"minimum": 1, "default": 1, "order": "newest_first"},
                "limit": {"minimum": 1, "maximum": 5, "default": 5},
                "year": {"required": False, "filters": "fiscal reporting year"},
            },
            "scope_page_size_offset": "No documented scope, page_size, or offset parameter. Probes are recorded below.",
            "query_probes": inventory["query_probes"],
            "full_history_observed_end": "Empty data array. The annual penultimate page had two records.",
            "sdk_pagination": "raw() follows pages automatically. only_page targets one page and page_cap bounds the number of pages.",
        },
    }
    return findings, history


def amount(row: Json, field: str) -> int | float | None:
    value = row.get(field)
    return (
        value
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def compare_flow(
    quarters: list[Json], years: list[Json], field: str, tolerance: int
) -> Json:
    """Compare sum(Q1..Q4) and Q4 separately to the annual amount in VND."""
    quarterly = {period_key(row, "quarter"): row for row in quarters}
    annual = {period_key(row, "year")[0]: row for row in years}
    if not quarterly:
        return {
            "raw_field": field,
            "counts": {},
            "by_year": [],
            "finding": "undetermined",
        }
    counts = dict.fromkeys(
        ("discrete_only", "ytd_only", "ambiguous", "neither", "missing"), 0
    )
    by_year: list[Json] = []
    for year in sorted({key[0] for key in quarterly}):
        q = {str(n): amount(quarterly.get((year, n), {}), field) for n in range(1, 5)}
        a = amount(annual.get(year, {}), field)
        entry: Json = {"year": year, "quarter_values_vnd": q, "annual_vnd": a}
        observed = [v for v in q.values() if v is not None]
        if len(observed) == 4 and a is not None:
            total = sum(observed)
            delta, q4_delta = total - a, observed[-1] - a
            discrete, ytd = abs(delta) <= tolerance, abs(q4_delta) <= tolerance
            outcome = (
                "ambiguous"
                if discrete and ytd
                else "discrete_only"
                if discrete
                else "ytd_only"
                if ytd
                else "neither"
            )
            entry.update(
                sum_quarters_vnd=total,
                sum_minus_annual_vnd=delta,
                q4_minus_annual_vnd=q4_delta,
            )
        else:
            outcome = "missing"
        counts[outcome] += 1
        by_year.append({**entry, "outcome": outcome})
    finding = "undetermined"
    if counts["discrete_only"] and not counts["ytd_only"]:
        finding = (
            "discrete_observed_with_historical_exceptions"
            if counts["neither"]
            else "discrete_observed"
        )
    elif counts["ytd_only"] and not counts["discrete_only"]:
        finding = "ytd_observed"
    elif counts["discrete_only"] and counts["ytd_only"]:
        finding = "mixed"
    return {
        "raw_field": field,
        "counts": counts,
        "by_year": by_year,
        "finding": finding,
    }


def check_balance(balances: dict[str, list[Json]]) -> Json:
    """Check VAS lines 270 = 300 + 400 using the mapped raw FHSC fields."""
    counts = dict.fromkeys(("pass", "fail", "missing"), 0)
    by_period = []
    maximum: int | float = 0
    for period in PERIODS:
        for row in balances[period]:
            year, quarter = period_key(row, period)
            assets, liabilities, equity = (
                amount(row, code) for code in BALANCE_FIELDS.values()
            )
            if assets is None or liabilities is None or equity is None:
                residual = None
                status = "missing"
            else:
                residual = assets - liabilities - equity
                status = "pass" if abs(residual) <= TOLERANCE_VND else "fail"
                maximum = max(maximum, abs(residual))
            counts[status] += 1
            by_period.append(
                {
                    "period": period,
                    "year": year,
                    "quarter": quarter,
                    "status": status,
                    "total_assets_vnd": assets,
                    "total_liabilities_vnd": liabilities,
                    "total_equity_vnd": equity,
                    "residual_vnd": residual,
                    "provider_total_funding_vnd": amount(row, "tongcongnguonvon"),
                }
            )
    return {
        "raw_fields": BALANCE_FIELDS,
        "tolerance_vnd": TOLERANCE_VND,
        "formula": "total_assets - total_liabilities - total_equity",
        "counts": counts,
        "max_absolute_residual_vnd": maximum,
        "by_period": by_period,
    }


def accounting(history: dict[str, dict[str, list[Json]]]) -> Json:
    reference = read_json(REFERENCE_PATH)["audited_annual_2025"]
    comparisons = []
    bindings = {
        label: ("balance-sheet", name) for label, name in BALANCE_FIELDS.items()
    }
    for label, (statement, code) in {**bindings, **FLOW_FIELDS}.items():
        expected = reference["values_vnd"][label]
        row = next((r for r in history[statement]["year"] if r["year"] == 2025), {})
        observed = amount(row, code)
        comparisons.append(
            {
                "metric": label,
                "raw_field": code,
                "pdf_page": expected["pdf_page"],
                "fhsc_vnd": observed,
                "pdf_vnd": expected["value"],
                "matching": observed == expected["value"],
            }
        )
    return {
        "flow_convention": {
            "unit": "VND",
            "tolerance_vnd": TOLERANCE_VND,
            "method": "sum(Q1+Q2+Q3+Q4) == annual tests discrete flows; Q4 == annual tests YTD.",
            "historical_mismatches": "These rows remain as received. Causes and revisions require separate evidence.",
            "metrics": {
                label: compare_flow(
                    history[statement]["quarter"],
                    history[statement]["year"],
                    code,
                    TOLERANCE_VND,
                )
                for label, (statement, code) in FLOW_FIELDS.items()
            },
        },
        "accounting_identity": check_balance(history["balance-sheet"]),
        "statement_scope": {
            "fpt_2025_annual": "CONSOLIDATED"
            if all(row["matching"] for row in comparisons)
            else "UNKNOWN",
            "basis": "Independent audited FPT consolidated PDF as reference for this FPT annual period.",
            "reference": reference["source"],
            "comparisons": comparisons,
            "default_for_unverified_records": "UNKNOWN",
        },
    }


class HttpObservations:
    """Collect HTTP responses and an allowlist of provider rate-limit headers."""

    def __init__(self) -> None:
        self.responses: list[Json] = []
        self.transport_waits: list[float] = []

    def on_response(self, response: Any) -> None:
        headers = {
            key.lower(): value[:256]
            for key, value in response.headers.items()
            if key.lower() in {"retry-after", "ratelimit", "ratelimit-policy"}
            or key.lower().startswith(
                ("x-ratelimit-", "x-rate-limit-", "ratelimit-", "rate-limit-")
            )
        }
        self.responses.append(
            {"status": response.status_code, "rate_limit_headers": headers}
        )

    def transport_sleep(self, seconds: float) -> None:
        self.transport_waits.append(seconds)
        sleep(seconds)


def benchmark(source: Any, traffic: HttpObservations) -> Json:
    """Measure 25 issuers x 3 statements x 2 periods, with one HTTP page per call."""
    started = perf_counter()
    initial_response_count = len(traffic.responses)
    initial_wait_count = len(traffic.transport_waits)
    previous_start = started - INTERVAL_SECONDS
    calls: list[Json] = []
    stopped = None
    try:
        for symbol in SYMBOLS:
            for statement in STATEMENTS:
                for period in PERIODS:
                    sleep(max(0.0, previous_start + INTERVAL_SECONDS - perf_counter()))
                    previous_start = perf_counter()
                    response_index = len(traffic.responses)
                    call: Json = {
                        "symbol": symbol,
                        "statement": statement,
                        "period": period,
                    }
                    try:
                        envelope, _ = fetch_page(source, symbol, statement, period, 1)
                        call.update(
                            http_status=envelope.http_status,
                            records=len(body_rows(envelope.body)),
                        )
                    except Exception as error:
                        call.update(error_details(error))
                    call["elapsed_ms"] = round(
                        (perf_counter() - previous_start) * 1000, 3
                    )
                    call["http_responses"] = traffic.responses[response_index:]
                    calls.append(call)
                    http_statuses = {
                        item["status"] for item in call["http_responses"]
                    } | {call.get("http_status")}
                    if 429 in http_statuses:
                        stopped = "rate_limit"
                    elif {401, 403}.intersection(http_statuses) or call.get(
                        "error_type"
                    ) == "CredentialError":
                        stopped = "entitlement"
                    elif "circuit" in call.get("error_type", "").lower():
                        stopped = "circuit_breaker"
                    if stopped:
                        break
                    if len(calls) % 25 == 0:
                        print(f"FHSC benchmark: {len(calls)}/150 calls", flush=True)
                if stopped:
                    break
            if stopped:
                break
    except KeyboardInterrupt:
        stopped = "interrupted"
    elapsed = perf_counter() - started
    responses = traffic.responses[initial_response_count:]
    waits = traffic.transport_waits[initial_wait_count:]
    statuses = Counter(str(item["status"]) for item in responses)
    headers: dict[str, set[str]] = {}
    for response in responses:
        for key, value in response["rate_limit_headers"].items():
            headers.setdefault(key, set()).add(value)
    times = [call["elapsed_ms"] for call in calls]
    p95 = (
        quantiles(times, n=100, method="inclusive")[94]
        if len(times) > 1
        else (times[0] if times else None)
    )
    successful = sum(
        call.get("http_status") == 200 and "records" in call for call in calls
    )
    planned = len(SYMBOLS) * len(STATEMENTS) * len(PERIODS)
    return {
        "status": "completed"
        if len(calls) == planned and stopped is None
        else "stopped",
        "stop_reason": stopped,
        "symbols": list(SYMBOLS),
        "page": 1,
        "limit": PAGE_SIZE,
        "planned_calls": planned,
        "attempted_calls": len(calls),
        "successful_calls": successful,
        "calls_with_data": sum(call.get("records", 0) > 0 for call in calls),
        "elapsed_seconds": round(elapsed, 3),
        "pacing_min_start_interval_seconds": INTERVAL_SECONDS,
        "p50_call_latency_ms": round(median(times), 3) if times else None,
        "p95_call_latency_ms": round(p95, 3) if p95 is not None else None,
        "latency_method": "Wall time inside each SDK call, including any SDK wait/retry but excluding application pacing. p95 uses linear interpolation.",
        "http_requests_observed": len(responses),
        "http_status_distribution": dict(sorted(statuses.items())),
        "calls_without_http_response": sum(
            not call["http_responses"] for call in calls
        ),
        "observed_http_responses_per_minute": round(len(responses) * 60 / elapsed, 2)
        if elapsed
        else None,
        "rate_limit_docs": RATE_DOCS_URL,
        "rate_limit_headers_observed": {
            key: sorted(values) for key, values in sorted(headers.items())
        },
        "rate_limit_observed": bool(statuses["429"]) or stopped == "rate_limit",
        "forbidden_observed": bool(statuses["403"]),
        "circuit_breaker_observed": stopped == "circuit_breaker",
        "sdk_transport_wait_count": len(waits),
        "sdk_transport_wait_seconds": round(sum(waits), 3),
        "sdk_retry_responses_observed": len(responses)
        - sum(bool(call["http_responses"]) for call in calls),
        "provider_quota_per_minute": None,
        "calls": calls,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    parser.add_argument("--history-dir", type=Path)
    parser.add_argument("--reuse-history", action="store_true")
    parser.add_argument(
        "--history-only", action="store_true", help="Skip the 150-call benchmark."
    )
    parser.add_argument(
        "--prompt-key",
        action="store_true",
        help="Read the FHSC key without terminal echo.",
    )
    args = parser.parse_args(argv)
    directory = args.history_dir or (
        HISTORY_PATH
        if args.reuse_history
        else HISTORY_PATH.with_name(
            f"{HISTORY_PATH.name}_{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
        )
    )
    report: Json = {
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "blocked",
        "source": SOURCE,
        "connector": "vnstock-oapi",
        "required_version": SDK_VERSION,
        "next_source": "FileAdapter",
        "signoff": "pending_human_review",
    }
    offline = args.reuse_history and args.history_only
    client = None
    try:
        if not offline:
            report["installed_version"] = version("vnstock-oapi")
            if report["installed_version"] != SDK_VERSION:
                raise ValueError("Unexpected vnstock-oapi version")
            if args.prompt_key:
                os.environ["FHSC_API_KEY"] = getpass.getpass("FHSC key (hidden): ")
            oapi = import_module("vnstock_oapi")
            httpx = import_module("httpx")
            transport_module = import_module("vnstock_oapi.core.transport")
            spec = oapi.source(SOURCE).spec
            traffic = HttpObservations()
            client = httpx.Client(
                verify=transport_module.tls_context(),
                timeout=httpx.Timeout(
                    transport_module.READ_TIMEOUT_S,
                    connect=transport_module.CONNECT_TIMEOUT_S,
                ),
                headers={"Accept": "application/json"},
                event_hooks={"response": [traffic.on_response]},
            )
            transport = transport_module.Transport(
                SOURCE,
                spec.hosts,
                rate_per_second=spec.rate_per_second,
                client=client,
                sleep=traffic.transport_sleep,
                user_agent=spec.user_agent,
            )
            source = oapi.core.source.Source(spec, transport=transport)
        if args.reuse_history:
            inventory = read_json(directory / "inventory.json")
        else:
            inventory = capture_history(source, directory, oapi.source)
        findings, raw_history = analyse_history(directory, inventory)
        report.update(findings)
        report.update(accounting(raw_history))
        report["entitlement"] = {
            "status": "accepted" if report["history"]["complete"] else "partial",
            "evidence": "FPT FHSC responses captured with the user's key.",
            "subscription_tier": None,
            "expires_at": None,
            "details": "The financial-statement endpoint does not return subscription or key-expiry metadata.",
        }
        report["metadata_contract"] = {
            "observed_body_fields": ["data", "error_code", "message", "traceId"],
            "observed_period_metadata": {
                "quarter": ["year", "quarter"],
                "year": ["year"],
            },
            "from_request": ["ticker", "statement_type", "report_period", "page"],
            "from_client_envelope": ["fetched_at", "http_status", "body_sha256"],
            "absent_from_sampled_fpt_bodies": [
                "ticker",
                "fiscal_period_start",
                "period_end",
                "publication_timestamp",
                "audit_status",
                "audit_opinion",
                "revision",
                "statement_scope",
                "currency_unit",
            ],
        }
        report["benchmark"] = (
            {"status": "not_run"} if args.history_only else benchmark(source, traffic)
        )
        if report["history"]["complete"]:
            report.update(status="history_verified", next_source="vnstock-oapi")
            result = report["benchmark"]
            if (
                result["status"] == "completed"
                and result["successful_calls"] == result["planned_calls"]
            ):
                report["status"] = "ready_for_signoff"
    except Exception as error:
        report["failure"] = error_details(error)
    finally:
        if client is not None:
            client.close()
        write_report(args.output, report)
    print(f"Gate 0 {report['status']}. Report written to {args.output}.", flush=True)
    return (
        0
        if report["status"] == "ready_for_signoff"
        or (args.history_only and report["status"] == "history_verified")
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
