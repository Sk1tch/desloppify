from __future__ import annotations

from types import SimpleNamespace

from desloppify.languages._framework.generic_parts.tool_runner import ToolRunResult
from desloppify.languages.cxx import phases as cxx_phases


def test_phase_cppcheck_retries_per_file_after_batch_timeout(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        cxx_phases,
        "find_cxx_files",
        lambda _path: ["src/unsafe_a.cpp", "src/unsafe_b.cpp"],
        raising=False,
    )

    def _fake_run_tool_result(cmd, path, parser, **_kwargs):
        assert path == tmp_path
        assert cmd.startswith("cppcheck ")
        calls.append(cmd)
        if "unsafe_a.cpp" in cmd and "unsafe_b.cpp" in cmd:
            return ToolRunResult(
                entries=[],
                status="error",
                error_kind="tool_timeout",
                message="timeout",
                returncode=1,
            )
        if "unsafe_a.cpp" in cmd:
            return ToolRunResult(
                entries=[
                    {
                        "file": "src/unsafe_a.cpp",
                        "line": 8,
                        "message": "Using 'system' can be unsafe",
                    }
                ],
                status="ok",
                returncode=1,
            )
        if "unsafe_b.cpp" in cmd:
            return ToolRunResult(entries=[], status="empty", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        cxx_phases,
        "run_tool_result",
        _fake_run_tool_result,
        raising=False,
    )

    lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])
    issues, signals = cxx_phases.phase_cppcheck_issue(tmp_path, lang)

    assert len(calls) == 3
    assert len(issues) == 1
    assert issues[0]["detector"] == "cppcheck_issue"
    assert issues[0]["summary"] == "Using 'system' can be unsafe"
    assert signals == {"cppcheck_issue": 1}
    assert lang.detector_coverage == {}
    assert lang.coverage_warnings == []


def test_phase_cppcheck_records_reduced_coverage_when_single_file_retry_still_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cxx_phases,
        "find_cxx_files",
        lambda _path: ["src/unsafe_a.cpp", "src/unsafe_b.cpp"],
        raising=False,
    )

    def _fake_run_tool_result(cmd, _path, _parser, **_kwargs):
        if "unsafe_a.cpp" in cmd and "unsafe_b.cpp" in cmd:
            return ToolRunResult(
                entries=[],
                status="error",
                error_kind="tool_timeout",
                message="timeout",
                returncode=1,
            )
        if "unsafe_a.cpp" in cmd:
            return ToolRunResult(
                entries=[],
                status="error",
                error_kind="tool_timeout",
                message="timeout",
                returncode=1,
            )
        if "unsafe_b.cpp" in cmd:
            return ToolRunResult(entries=[], status="empty", returncode=0)
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(
        cxx_phases,
        "run_tool_result",
        _fake_run_tool_result,
        raising=False,
    )

    lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])
    issues, signals = cxx_phases.phase_cppcheck_issue(tmp_path, lang)

    assert issues == []
    assert signals == {}
    assert lang.detector_coverage["cppcheck_issue"]["status"] == "reduced"
    assert lang.detector_coverage["cppcheck_issue"]["reason"] == "tool_timeout"
    assert lang.coverage_warnings[0]["detector"] == "cppcheck_issue"

def test_phase_cppcheck_uses_unique_issue_ids_for_same_line(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cxx_phases,
        "find_cxx_files",
        lambda _path: ["src/unsafe.cpp"],
        raising=False,
    )

    def _fake_run_tool_result(_cmd, _path, _parser, **_kwargs):
        return ToolRunResult(
            entries=[
                {
                    "file": "src/unsafe.cpp",
                    "line": 8,
                    "message": "Using 'system' can be unsafe",
                },
                {
                    "file": "src/unsafe.cpp",
                    "line": 8,
                    "message": "Buffer overflow risk",
                },
            ],
            status="ok",
            returncode=1,
        )

    monkeypatch.setattr(
        cxx_phases,
        "run_tool_result",
        _fake_run_tool_result,
        raising=False,
    )

    lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])
    issues, signals = cxx_phases.phase_cppcheck_issue(tmp_path, lang)

    assert len(issues) == 2
    assert issues[0]["id"] != issues[1]["id"]
    assert signals == {"cppcheck_issue": 2}


def test_cppcheck_configuration_failure_retries_files_and_preserves_reliable_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(cxx_phases, "find_cxx_files", lambda _: ["broken.cpp", "clean.cpp"])
    calls = []

    def fake_run(cmd, _path, _parser):
        calls.append(cmd)
        if "broken.cpp" in cmd:
            return ToolRunResult(entries=[{
                "file": "broken.cpp", "line": 5,
                "message": "There is an unknown macro here somewhere. Configuration is required. If GENERATED_BODY is a macro then please configure it.",
            }], status="ok", returncode=0)
        return ToolRunResult(entries=[{"file": "clean.cpp", "line": 7, "message": "Variable x is unused"}], status="ok", returncode=0)

    monkeypatch.setattr(cxx_phases, "run_tool_result", fake_run)
    lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])
    issues, signals = cxx_phases.phase_cppcheck_issue(tmp_path, lang)
    assert len(calls) == 3
    assert [i["summary"] for i in issues] == ["Variable x is unused"]
    assert signals == {"cppcheck_issue": 1}
    assert lang.detector_coverage["cppcheck_issue"]["reason"] == "tool_configuration"


def test_cppcheck_missing_include_is_coverage_failure_not_code_issue(tmp_path, monkeypatch):
    monkeypatch.setattr(cxx_phases, "find_cxx_files", lambda _: ["controller.cpp"])
    monkeypatch.setattr(cxx_phases, "run_tool_result", lambda *_: ToolRunResult(entries=[{
        "file": "controller.cpp", "line": 11,
        "message": 'information: Include file: "Widgets/Input/SVirtualJoystick.h" not found.',
    }], status="ok", returncode=0))
    lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])
    issues, signals = cxx_phases.phase_cppcheck_issue(tmp_path, lang)
    assert issues == []
    assert signals == {}
    assert lang.detector_coverage["cppcheck_issue"]["status"] == "reduced"


def test_cppcheck_cpp_header_parsed_as_c_is_configuration_failure_only_for_headers(tmp_path, monkeypatch):
    for filename, reduced in [("widget.hpp", True), ("widget.c", False)]:
        monkeypatch.setattr(cxx_phases, "find_cxx_files", lambda _, file=filename: [file])
        monkeypatch.setattr(cxx_phases, "run_tool_result", lambda *_, file=filename: ToolRunResult(entries=[{
            "file": file, "line": 4, "message": "error: Code 'template<...' is invalid C code.",
        }], status="ok", returncode=0))
        lang = SimpleNamespace(detector_coverage={}, coverage_warnings=[])
        issues, _ = cxx_phases.phase_cppcheck_issue(tmp_path, lang)
        assert bool(lang.detector_coverage) is reduced
        assert len(issues) == (0 if reduced else 1)
