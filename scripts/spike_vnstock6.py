"""Probe the FHSC financial statements exposed by vnstock 6.0.1.

Run with `python scripts/spike_vnstock6.py` in the spike environment. The default
run attempts all three statements for 50 symbols. Use `--sample-only` for FPT.
Exit 0 when the evidence qualifies FHSC and 1 when Gate 0 needs more evidence.
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import re
import statistics
import warnings
from datetime import UTC, datetime
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from numbers import Real
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data/metadata/spike_report.json"
REFERENCE_PATH = ROOT / "tests/fixtures/raw_samples/fpt_2025_spike_reference.json"
EXPECTED_VERSION = "6.0.1"
SOURCE = "fhsc"
STATEMENTS = ("balance_sheet", "income_statement", "cash_flow")
SYMBOLS = tuple(
    """
    FPT VNM HPG VCB ACB TCB MBB BID CTG VPB STB HDB VIB TPB SHB EIB
    SSI HCM VND VCI VIX BVH PVI BMI MWG FRT PNJ DGW MSN SAB
    GAS PLX PVD PVS BSR GMD HAH VSC VJC HVN VRE VHM VIC KDH
    NLG DGC DCM DPM REE POW
    """.split()
)
QUARTERS = ("2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4")
SCOPE_FIELDS = {
    "scope",
    "statement_scope",
    "report_scope",
    "consolidated",
    "is_consolidated",
}
VENDOR_CODE = re.compile(r"\bVNSTOCK_[A-Z0-9_]+\b")


def new_report(symbols: tuple[str, ...]) -> dict[str, Any]:
    """Create a report that can also describe a run blocked before any SDK call."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "source": SOURCE,
        "required_vnstock_version": EXPECTED_VERSION,
        "status": "blocked",
        "next_source": "FileAdapter",
        "fallback_path": "tests/fixtures/raw_samples/",
        "entitlement": {
            "status": "not_tested",
            "fhsc_env_key_present": bool(os.getenv("FHSC_API_KEY")),
            "fhsc_key_present": None,
            "fhsc_plan": None,
            "fhsc_plan_basis": "The SDK key status does not expose the FHSC subscription.",
            "vnstock_account_tier": None,
            "key_rotation": None,
            "key_rotation_warning_observed": None,
        },
        "schema_metadata": {name: {"status": "not_observed"} for name in STATEMENTS},
        "statement_scope": {name: {"status": "not_observed"} for name in STATEMENTS},
        "flow_convention": {
            name: {"status": "not_observed"}
            for name in ("income_statement", "cash_flow")
        },
        "stress": {
            "status": "not_run",
            "planned_symbols": list(symbols),
            "planned_calls": len(symbols) * len(STATEMENTS),
            "attempted_calls": 0,
            "calls": [],
            "rate_limit_observed": None,
            "circuit_open_observed": None,
            "http_requests_per_minute": None,
            "rate_note": "Measures SDK calls. One call can paginate across multiple HTTP requests.",
        },
    }


def vendor_code(value: object) -> str | None:
    match = VENDOR_CODE.search(value) if isinstance(value, str) else None
    return match.group() if match else None


def error_details(exc: Exception) -> dict[str, Any]:
    """Keep diagnostic fields that need no raw vendor error text."""
    http_status = getattr(exc, "http_status", None)
    retry_after = getattr(exc, "retry_after", None)
    return {
        "type": type(exc).__name__,
        "code": vendor_code(getattr(exc, "code", None)),
        "http_status": http_status if isinstance(http_status, int) else None,
        "retry_after_seconds": (
            retry_after
            if isinstance(retry_after, (int, float)) and math.isfinite(retry_after)
            else None
        ),
    }


def stop_reason(error: dict[str, Any]) -> str | None:
    error_type, code, status = error["type"], error["code"], error["http_status"]
    if error_type == "CircuitOpenError" or (code and "CIRCUIT_OPEN" in code):
        return "circuit_open"
    if (
        status == 429
        or code == "VNSTOCK_SOURCE_RATE_LIMITED"
        or error_type in {"RateLimitError", "RateLimitedError"}
    ):
        return "rate_limit"
    if status in {401, 403} or error_type in {
        "CredentialError",
        "AuthError",
        "LoginRequiredError",
        "OtpRequiredError",
    }:
        return "entitlement"
    if error_type in {"PolicyError", "AttributeError", "TypeError"}:
        return "sdk_error"
    return None


def match_reference(
    values: dict[str, float | None],
    candidates: dict[str, dict[str, dict[str, int]]],
) -> list[dict[str, str]]:
    """Require Q2 and Q3 to match a candidate within one VND.

    Check Q1 and Q4 too when both sources have values for those periods.
    """
    if any(values.get(period) is None for period in QUARTERS[1:3]):
        return []

    matches = []
    for scope, by_convention in candidates.items():
        for convention, expected in by_convention.items():
            if set(QUARTERS[1:3]).issubset(expected) and all(
                math.isclose(value, expected[period], rel_tol=0, abs_tol=1)
                for period, value in values.items()
                if period in expected and value is not None
            ):
                matches.append({"scope": scope, "convention": convention})
    return matches


def inspect_sample(
    statement: str, frame: Any, call: Any, report: dict[str, Any]
) -> None:
    """Record observed DataFrame fields and compare the FPT values with its filings."""
    columns = [str(column) for column in frame.columns]
    attrs = frame.attrs
    taxonomy = attrs.get("taxonomy")
    identities = attrs.get("identities")
    report["schema_metadata"][statement] = {
        "status": "observed",
        "columns": columns,
        "attrs_keys": sorted(map(str, attrs)),
        "row_count": len(frame),
        "taxonomy_variant": taxonomy.get("variant")
        if isinstance(taxonomy, dict)
        else None,
        "taxonomy_version": attrs.get("taxonomy_version"),
        "identity_counts": {
            key: identities[key]
            for key in ("checked", "passed", "known_breaks")
            if isinstance(identities, dict) and isinstance(identities.get(key), int)
        },
    }
    try:
        parameters = list(inspect.signature(call).parameters)
    except (TypeError, ValueError):
        parameters = []

    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    line = reference["lines"][statement]
    values: dict[str, float | None] = dict.fromkeys(QUARTERS)
    required = {"report_period", "line_code", "item_value"}
    if required.issubset(columns):
        rows = frame.loc[frame["line_code"] == line["line_code"]]
        for period in QUARTERS:
            result = rows.loc[rows["report_period"] == period, "item_value"]
            if len(result) == 1:
                value = result.iloc[0]
                if isinstance(value, Real) and not isinstance(value, bool):
                    number = float(value)
                    values[period] = number if math.isfinite(number) else None

    matches = match_reference(values, line["expected_vnd"])
    scopes = {match["scope"] for match in matches}
    report["statement_scope"][statement] = {
        "status": "compared" if matches else "inconclusive",
        "parameters": parameters,
        "candidate_scope_parameters": sorted(SCOPE_FIELDS.intersection(parameters)),
        "candidate_scope_fields": sorted(SCOPE_FIELDS.intersection([*columns, *attrs])),
        "fpt_scope_matching_pdf": scopes.pop() if len(scopes) == 1 else None,
    }
    comparison = {
        "line_code": line["line_code"],
        "observed_vnd": values,
        "expected_vnd": line["expected_vnd"],
        "pdf_pages": line["pdf_pages"],
        "matches": matches,
    }
    if statement == "balance_sheet":
        report["statement_scope"][statement]["pdf_comparison"] = comparison
    else:
        report["flow_convention"][statement] = {
            **comparison,
            "status": "compared" if len(matches) == 1 else "inconclusive",
            "convention": matches[0]["convention"] if len(matches) == 1 else None,
        }
    report["fpt_pdf_sources"] = reference["sources"]


def probe(api: Any, symbols: tuple[str, ...], report: dict[str, Any]) -> None:
    """Call the statements sequentially and stop at rate, circuit or access errors."""
    stress = report["stress"]
    entitlement = report["entitlement"]
    calls: list[dict[str, Any]] = stress["calls"]
    start = perf_counter()
    reason = None
    for symbol in symbols:
        for statement in STATEMENTS:
            elapsed = perf_counter()
            call_record: dict[str, Any] = {"symbol": symbol, "statement": statement}
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                try:
                    call = getattr(api.equity(symbol), statement)
                    frame = call(
                        period="quarter", source=SOURCE, format="long", compat="v6"
                    )
                    call_record["row_count"] = len(frame)
                except Exception as exc:
                    error = error_details(exc)
                    call_record["error"] = error
                    reason = stop_reason(error)
                    if reason == "entitlement":
                        entitlement["status"] = "denied"
                else:
                    entitlement["status"] = "accepted"
                    entitlement["fhsc_key_present"] = True
                    if symbol == "FPT":
                        try:
                            inspect_sample(statement, frame, call, report)
                        except (
                            AttributeError,
                            OSError,
                            TypeError,
                            ValueError,
                            KeyError,
                        ) as exc:
                            call_record["sample_inspection_error"] = error_details(exc)
                finally:
                    call_record["elapsed_seconds"] = round(perf_counter() - elapsed, 6)
                    call_record["warnings"] = [
                        {
                            "type": item.category.__name__,
                            "code": vendor_code(getattr(item.message, "code", None))
                            or vendor_code(str(item.message)),
                        }
                        for item in captured
                    ]
                    calls.append(call_record)
            if reason:
                break
        if reason:
            break

    duration = perf_counter() - start
    entitlement["key_rotation_warning_observed"] = any(
        warning["type"] == "KeyRotationWarning"
        or warning["code"] == "VNSTOCK_KEY_ROTATION"
        for call_record in calls
        for warning in call_record["warnings"]
    )
    latencies = [result["elapsed_seconds"] for result in calls]
    stress.update(
        status="stopped"
        if reason
        else ("sample_only" if len(symbols) == 1 else "completed"),
        stop_reason=reason,
        attempted_calls=len(calls),
        successful_calls=sum("row_count" in result for result in calls),
        elapsed_seconds=round(duration, 6),
        median_call_seconds=statistics.median(latencies) if latencies else None,
        sdk_calls_per_minute=round(len(calls) * 60 / duration, 2)
        if duration and calls
        else None,
        rate_limit_observed=None
        if reason in {"entitlement", "sdk_error"}
        else reason == "rate_limit",
        circuit_open_observed=(
            None if reason in {"entitlement", "sdk_error"} else reason == "circuit_open"
        ),
    )


def survey(symbols: tuple[str, ...]) -> dict[str, Any]:
    """Check the installed release and account, then collect evidence for Gate 0."""
    report = new_report(symbols)
    try:
        installed = version("vnstock")
    except PackageNotFoundError:
        installed = None
    report["installed_vnstock_version"] = installed
    if installed != EXPECTED_VERSION:
        report["blocked_reason"] = (
            "missing_vnstock" if installed is None else "wrong_vnstock_version"
        )
        return report

    entitlement = report["entitlement"]
    try:
        oapi = import_module("vnstock_oapi")
        local = oapi.source(SOURCE).status()
        entitlement["fhsc_key_present"] = isinstance(local["keys"], dict) and bool(
            local["keys"]
        )
        rotation = local.get("key_rotation")
        if isinstance(rotation, dict):
            entitlement["key_rotation"] = {
                key: rotation[key]
                for key in ("valid_days", "stored_on", "due_on", "days_left")
                if key in rotation
            }
    except Exception as exc:
        entitlement["local_key_check_error"] = error_details(exc)

    try:
        sdk = import_module("vnstock")
        api = sdk.Fundamental()
    except Exception as exc:
        app_control = (
            isinstance(exc, ImportError)
            and "application control policy" in str(exc).lower()
        )
        report["blocked_reason"] = (
            "application_control_blocked_vnstock"
            if app_control
            else "sdk_initialization_failed"
        )
        report["sdk_error"] = error_details(exc)
        return report

    try:
        entitlement["vnstock_account_tier"] = sdk.account.status().get("tier")
    except Exception as exc:
        entitlement["vnstock_account_check_error"] = error_details(exc)

    probe(api, symbols, report)
    if entitlement["status"] != "accepted":
        report["blocked_reason"] = (
            report["stress"]["stop_reason"] or "no_successful_fhsc_calls"
        )
        return report

    scope_set = {
        result.get("fpt_scope_matching_pdf")
        for result in report["statement_scope"].values()
    }
    stress = report["stress"]
    has_scopes = len(scope_set) == 1 and None not in scope_set
    has_flows = all(
        result.get("convention") for result in report["flow_convention"].values()
    )
    has_stress_evidence = len(symbols) > 1 and (
        stress["status"] == "completed"
        or stress["stop_reason"] in {"rate_limit", "circuit_open"}
    )
    if has_scopes and has_flows and has_stress_evidence:
        report.update(status="qualified", next_source="vnstock")
    else:
        report["status"] = "inconclusive"
    return report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as temp_dir:
        temp_path = Path(temp_dir) / path.name
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(report, file, indent=2, ensure_ascii=False, allow_nan=False)
            file.write("\n")
        temp_path.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-only", action="store_true", help="Probe the three FPT statements."
    )
    parser.add_argument(
        "--output", type=Path, default=REPORT_PATH, help="JSON report path."
    )
    args = parser.parse_args(argv)
    report = survey(("FPT",) if args.sample_only else SYMBOLS)
    write_report(args.output, report)
    print(f"Gate 0 {report['status']}. Report written to {args.output}")
    return 0 if report["status"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
