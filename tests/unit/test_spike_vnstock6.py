import json
import warnings
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace

from pytest import MonkeyPatch

from scripts import spike_vnstock6 as spike


def test_missing_sdk_writes_fallback_report(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(spike, "version", missing)
    path = tmp_path / "spike_report.json"
    assert spike.main(["--output", str(path)]) == 1

    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["next_source"] == "FileAdapter"
    assert report["entitlement"]["status"] == "not_tested"
    assert report["stress"]["attempted_calls"] == 0


def test_checks_fhsc_key_even_when_windows_blocks_sdk(monkeypatch: MonkeyPatch) -> None:
    def load(name: str) -> SimpleNamespace:
        if name == "vnstock_oapi":
            return SimpleNamespace(
                source=lambda _: SimpleNamespace(status=lambda: {"keys": "missing"}),
            )
        raise ImportError(
            "DLL load failed: An Application Control policy has blocked this file."
        )

    monkeypatch.setattr(spike, "version", lambda _: spike.EXPECTED_VERSION)
    monkeypatch.setattr(spike, "import_module", load)
    report = spike.survey(spike.SYMBOLS)

    assert report["blocked_reason"] == "application_control_blocked_vnstock"
    assert report["entitlement"]["fhsc_key_present"] is False
    assert report["stress"]["attempted_calls"] == 0


def test_stops_on_rate_limit_and_records_warning_without_message() -> None:
    class RateLimitError(Exception):
        http_status = 429
        retry_after = 60

    class KeyRotationWarning(UserWarning):
        code = "VNSTOCK_KEY_ROTATION"

    attempted: list[str] = []

    def balance_sheet(*, period: str, source: str, format: str, compat: str) -> None:
        attempted.append(source)
        warnings.warn("untrusted warning text", KeyRotationWarning, stacklevel=1)
        raise RateLimitError("untrusted error text")

    api = SimpleNamespace(equity=lambda _: SimpleNamespace(balance_sheet=balance_sheet))
    report = spike.new_report(spike.SYMBOLS)
    spike.probe(api, spike.SYMBOLS, report)

    assert attempted == ["fhsc"]
    assert report["stress"]["stop_reason"] == "rate_limit"
    assert report["stress"]["attempted_calls"] == 1
    assert report["entitlement"]["key_rotation_warning_observed"] is True
    assert report["stress"]["calls"][0]["error"]["retry_after_seconds"] == 60
    assert "untrusted" not in json.dumps(report)


def test_stress_attempts_three_calls_per_symbol(monkeypatch: MonkeyPatch) -> None:
    def statement(**options: str) -> list[int]:
        assert options == {
            "period": "quarter",
            "source": "fhsc",
            "format": "long",
            "compat": "v6",
        }
        return [1]

    api = SimpleNamespace(
        equity=lambda _: SimpleNamespace(**dict.fromkeys(spike.STATEMENTS, statement)),
    )
    monkeypatch.setattr(spike, "inspect_sample", lambda *_: None)
    report = spike.new_report(spike.SYMBOLS)
    spike.probe(api, spike.SYMBOLS, report)

    stress = report["stress"]
    assert stress["status"] == "completed"
    assert stress["attempted_calls"] == stress["successful_calls"] == 150
    assert [(call["symbol"], call["statement"]) for call in stress["calls"]] == [
        (symbol, statement)
        for symbol in spike.SYMBOLS
        for statement in spike.STATEMENTS
    ]


def test_reference_comparison_identifies_scope_and_convention() -> None:
    expected = {
        "CONSOLIDATED": {
            "quarter": {"2025-Q2": 20, "2025-Q3": 30},
            "ytd": {"2025-Q2": 30, "2025-Q3": 60},
        },
        "SEPARATE": {
            "quarter": {"2025-Q2": 2, "2025-Q3": 3},
            "ytd": {"2025-Q2": 3, "2025-Q3": 6},
        },
    }
    assert spike.match_reference({"2025-Q2": 30, "2025-Q3": 60}, expected) == [
        {"scope": "CONSOLIDATED", "convention": "ytd"},
    ]
    assert spike.match_reference({"2025-Q2": 30, "2025-Q3": None}, expected) == []
    assert spike.match_reference(
        {"2025-Q2": 30, "2025-Q3": 60, "2025-Q4": 99}, expected
    ) == [
        {"scope": "CONSOLIDATED", "convention": "ytd"},
    ]
    assert (
        spike.match_reference({"2025-Q2": 30, "2025-Q3": 60}, {"SEPARATE": {"ytd": {}}})
        == []
    )


def test_cash_flow_quarters_reconcile_to_fpt_pdf_ytd() -> None:
    reference = json.loads(spike.REFERENCE_PATH.read_text(encoding="utf-8"))
    assert reference["symbol"] == "FPT"
    assert reference["unit"] == "VND"
    for scope in reference["lines"]["cash_flow"]["expected_vnd"].values():
        cumulative = 0
        for period in spike.QUARTERS:
            cumulative += scope["quarter"][period]
            assert scope["ytd"][period] == cumulative
