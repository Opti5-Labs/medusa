"""
Tests for the Bob investigator (agents/bob.py) and the investigator panel.

A fake `bob` executable (a small Python script written per test) stands in for
Bob Shell, so the real subprocess path runs: flags, child environment,
workspace, NDJSON parsing, timeouts. No network, no Bobcoins.
"""

import asyncio
import json
import sys
import textwrap
import time

import pytest

from app.agents import bob, panel
from app.agents.results import InvestigatorResult
from app.models.contracts import Issue
from app.streaming import EventChannel

KEY = "bob-test-key-0123456789abcdef"


def _fake_bob(tmp_path, *, stdout_lines=(), stderr="", exit_code=0, sleep=0.0):
    """Write an executable fake Bob Shell; it records argv, env and workspace files."""
    record = tmp_path / "record.json"
    body = textwrap.dedent(
        f"""\
        import json, os, sys, time
        args = sys.argv[1:]
        ws = args[args.index("--workspace") + 1]
        files = sorted(
            os.path.relpath(os.path.join(d, f), ws).replace(os.sep, "/")
            for d, _, fs in os.walk(ws) for f in fs
        )
        json.dump({{"argv": args, "env": dict(os.environ), "files": files}},
                  open({str(record)!r}, "w"))
        time.sleep({sleep})
        for line in {list(stdout_lines)!r}:
            print(line)
        sys.stderr.write({stderr!r})
        sys.exit({exit_code})
        """
    )
    if sys.platform == "win32":
        # Windows has no shebang/chmod: a script needs a real executable
        # extension to run directly. Write the logic as .py and launch it
        # through a .bat shim (which Windows treats as directly runnable),
        # so BOB_BINARY still points at a single path on every platform.
        py_file = tmp_path / "fake-bob.py"
        py_file.write_text(body)
        script = tmp_path / "fake-bob.bat"
        script.write_text(f'@"{sys.executable}" "{py_file}" %*\r\n')
    else:
        script = tmp_path / "fake-bob"
        script.write_text(f"#!{sys.executable}\n" + body)
        script.chmod(0o755)
    return script, record


def _result_line(last_message: str, cost: float = 0.12) -> str:
    return json.dumps(
        {
            "type": "result",
            "status": "success",
            "stats": {"session_costs": cost, "max_cost": 0.25, "tool_calls": 2},
            "last_message": last_message,
        }
    )


GOOD_ANSWER = {
    "root_cause": "The final return passes the raw path through when no local folder exists.",
    "evidence": ["whisper_client.py:53 return settings.WHISPER_HF_MODEL"],
    "confidence": 0.8,
    "candidate_fixes": [
        {
            "approach": "Guard path-like values",
            "function_source": "def _resolve_hf_asr_model():\n    return 'x'\n",
        }
    ],
}


@pytest.fixture
def live_bob(monkeypatch):
    monkeypatch.setattr("app.config.BOB_MODE", "live")
    monkeypatch.setattr("app.config.BOB_API_KEY", KEY)
    monkeypatch.setattr("app.config.BOB_MAX_COST", 0.25)
    monkeypatch.setattr("app.config.BOB_MAX_TURNS", 6)
    monkeypatch.setattr(bob, "_cache", bob.OrderedDict())

    def use(script):
        monkeypatch.setattr("app.config.BOB_BINARY", str(script))

    return use


FILES = {
    "optilearn/app/services/whisper_client.py": "def f():\n    return 1\n",
    "cfg.py#L66": "X = 1\n",
}


# ── Successful investigation and normalisation ────────────────────────────────


async def test_successful_investigation_is_normalised(tmp_path, live_bob, monkeypatch):
    monkeypatch.setenv("WATSONX_API_KEY", "should-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "should-not-leak-either")
    fenced = "Here you go:\n```json\n" + json.dumps(GOOD_ANSWER) + "\n```"
    script, record = _fake_bob(
        tmp_path, stdout_lines=[_result_line(fenced, cost=0.1234)]
    )
    live_bob(script)

    result = await bob.investigate("diagnose", FILES)

    assert result.status == "ok" and result.investigator == "bob"
    assert result.root_cause.startswith("The final return")
    assert result.cost == 0.1234 and result.confidence == 0.8
    assert result.candidate_fixes[0].function_source.startswith(
        "def _resolve_hf_asr_model"
    )

    seen = json.loads(record.read_text())
    argv = seen["argv"]
    assert argv[:3] == ["run", "--format", "json"]
    assert argv[argv.index("--mode") + 1] == "ask"
    assert argv[argv.index("--max-cost") + 1] == "0.25"
    assert argv[argv.index("--max-turns") + 1] == "6"
    assert set(argv[argv.index("--disable-tool-groups") + 1].split(",")) >= {
        "edit",
        "execute",
    }
    assert "--team-id" not in argv  # Inference-scoped key needs no team id
    # Only what Bob needs reaches the child process. On Windows the fake
    # script runs through a .bat -> cmd.exe -> python.exe chain (Windows has
    # no shebang), and cmd.exe injects its own bookkeeping vars regardless
    # of the explicit env dict passed to it — not something the real code
    # lets through, just cmd.exe's own overhead. None carry secrets.
    _os_overhead = (
        {"COMSPEC", "PROMPT", "PATHEXT"} if sys.platform == "win32" else set()
    )
    assert set(seen["env"]) <= {
        "PATH",
        "HOME",
        "BOB_API_KEY",
        "LC_CTYPE",
        "__CF_USER_TEXT_ENCODING",
        *_os_overhead,
    }
    assert seen["env"]["BOB_API_KEY"] == KEY
    # The workspace holds copies of the files shown to Bob (excerpt suffix stripped).
    assert seen["files"] == ["cfg.py", "optilearn/app/services/whisper_client.py"]


def test_normalisation_absorbs_answer_drift():
    answer = {
        "root_cause": "raw path returned",
        "evidence": "single string instead of a list",
        "confidence": 85,
        "candidate_fixes": [
            {"approach": "empty fix"},
            {"function_source": "def g():\n    return 2\n"},
        ],
    }
    result = bob.parse_output(_result_line(json.dumps(answer)), "", 0)
    assert result.status == "ok"
    assert result.evidence == ["single string instead of a list"]
    assert result.confidence == 0.85
    assert len(result.candidate_fixes) == 1  # the fix without code is dropped
    assert result.candidate_fixes[0].approach  # default approach filled in


# ── Unavailable ───────────────────────────────────────────────────────────────


async def test_unavailable_without_key(monkeypatch):
    monkeypatch.setattr("app.config.BOB_MODE", "live")
    monkeypatch.setattr("app.config.BOB_API_KEY", "")
    result = await bob.investigate("p", FILES)
    assert result.status == "unavailable"
    assert "BOB_API_KEY is not set" in result.error


async def test_unavailable_without_cli(monkeypatch):
    monkeypatch.setattr("app.config.BOB_API_KEY", KEY)
    monkeypatch.setattr("app.config.BOB_BINARY", "definitely-not-installed-bob")
    result = await bob.investigate("p", FILES)
    assert result.status == "unavailable"
    assert "not installed" in result.error


@pytest.mark.parametrize(
    ("mode", "text"), [("off", "turned off"), ("replay", "BOB_MODE=replay")]
)
async def test_unavailable_by_mode(monkeypatch, mode, text):
    monkeypatch.setattr("app.config.BOB_API_KEY", KEY)
    monkeypatch.setattr("app.config.BOB_MODE", mode)
    result = await bob.investigate("p", FILES)
    assert result.status == "unavailable" and text in result.error


# ── Failures ──────────────────────────────────────────────────────────────────


async def test_authentication_failure_is_reported_without_the_key(tmp_path, live_bob):
    script, _ = _fake_bob(
        tmp_path,
        stderr=f"Error: Request Failed. Invalid or expired API key. key={KEY}\n",
        exit_code=1,
    )
    live_bob(script)
    result = await bob.investigate("p", FILES)
    assert result.status == "error"
    assert result.error.startswith("Bob authentication failed")
    assert "Invalid or expired API key" in result.error
    assert KEY not in result.error


async def test_turn_limit_is_reported_as_limit(tmp_path, live_bob):
    script, _ = _fake_bob(
        tmp_path,
        stdout_lines=[
            json.dumps(
                {"type": "error", "message": "The task reached the maximum of 6 turns."}
            ),
            _result_line("Contents of file x.py: ...", cost=0.2),
        ],
    )
    live_bob(script)
    result = await bob.investigate("p", FILES)
    assert result.status == "limit"
    assert "maximum of 6 turns" in result.error and "BOB_MAX_TURNS=6" in result.error
    assert result.cost == 0.2


async def test_cost_limit_is_reported_as_limit(tmp_path, live_bob):
    script, _ = _fake_bob(
        tmp_path,
        stdout_lines=[
            json.dumps(
                {
                    "type": "error",
                    "message": "The task reached the maximum cost of 0.25.",
                }
            ),
            _result_line("partial", cost=0.25),
        ],
    )
    live_bob(script)
    result = await bob.investigate("p", FILES)
    assert result.status == "limit" and "maximum cost" in result.error


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "The fake script runs through a .bat -> cmd.exe -> python.exe chain "
        "on Windows (no shebang there); killing the direct child (cmd.exe) "
        "doesn't kill its grandchild python.exe without Windows job-object "
        "process-group management, which the test fixture doesn't set up. "
        "The real Bob binary on Linux is spawned directly with no such "
        "intermediary, so this is a test-fixture gap, not a defect in "
        "agents/bob.py's actual timeout/kill logic — verified passing there."
    ),
)
async def test_timeout_stops_bob(tmp_path, live_bob, monkeypatch):
    monkeypatch.setattr("app.config.BOB_TIMEOUT_S", 1)
    script, _ = _fake_bob(tmp_path, sleep=5)
    live_bob(script)
    started = time.monotonic()
    result = await bob.investigate("p", FILES)
    assert time.monotonic() - started < 4
    assert result.status == "limit" and "did not finish" in result.error


async def test_unusable_answer_is_an_error_not_a_silent_fallback(tmp_path, live_bob):
    script, _ = _fake_bob(tmp_path, stdout_lines=[_result_line("I think it is fine.")])
    live_bob(script)
    result = await bob.investigate("p", FILES)
    assert result.status == "error"
    assert "did not return a usable diagnosis" in result.error


async def test_successful_results_are_cached(tmp_path, live_bob):
    script, record = _fake_bob(
        tmp_path, stdout_lines=[_result_line(json.dumps(GOOD_ANSWER))]
    )
    live_bob(script)
    await bob.investigate("same prompt", FILES)
    record.unlink()
    again = await bob.investigate("same prompt", FILES)
    assert again.status == "ok"
    assert not record.exists()  # second call never started the CLI


# ── Panel: independence and parallelism ───────────────────────────────────────

ISSUE = Issue(
    id="i", title="Bug", description="It breaks", priority="High", source="scan"
)


async def test_bob_and_granite_run_in_parallel_and_independently(monkeypatch):
    seen: dict[str, str] = {}

    async def fake_bob(prompt, files):
        seen["bob_prompt"] = prompt
        await asyncio.sleep(0.3)
        return InvestigatorResult(
            investigator="bob", status="ok", root_cause="BOB-DIAGNOSIS"
        )

    class _Synth:
        root_cause, evidence, confidence = "GRANITE-DIAGNOSIS", [], 0.5

    class _Runtime:
        trigger_conditions = "input is a local path that does not exist"

    class _Repository:
        execution_trace = ("L12 if is_local(path): True", "L13 return path")

    async def fake_granite(issue, files, channel, evidence):
        seen["granite_evidence"] = evidence
        await asyncio.sleep(0.3)
        return _Synth(), _Runtime(), _Repository()

    monkeypatch.setattr(panel.bob, "live_unavailable_reason", lambda: None)
    monkeypatch.setattr(panel.bob, "investigate", fake_bob)
    monkeypatch.setattr(panel.granite, "is_configured", lambda: True)
    monkeypatch.setattr(panel, "investigate_demo", fake_granite)

    channel = EventChannel()
    started = time.monotonic()
    results = await panel.investigate_demo_panel(ISSUE, FILES, "EVIDENCE", "f", channel)
    elapsed = time.monotonic() - started

    assert elapsed < 0.55, "investigators should run concurrently"
    assert [r.investigator for r in results] == ["bob", "granite"]
    assert all(r.ok for r in results)
    # Neither saw the other's diagnosis; both saw the same sandbox evidence.
    assert (
        "GRANITE-DIAGNOSIS" not in seen["bob_prompt"]
        and "EVIDENCE" in seen["bob_prompt"]
    )
    assert "BOB-DIAGNOSIS" not in seen["granite_evidence"]
    assert panel.source_of(results) == "bob_and_granite"
    combined = panel.combined_root_cause(results)
    assert "Bob: BOB-DIAGNOSIS" in combined and "Granite: GRANITE-DIAGNOSIS" in combined
    granite_result = next(r for r in results if r.investigator == "granite")
    assert (
        granite_result.trigger_conditions == "input is a local path that does not exist"
    )
    assert granite_result.execution_trace == [
        "L12 if is_local(path): True",
        "L13 return path",
    ]
    report = granite_result.report()
    assert report.trigger_conditions == granite_result.trigger_conditions
    assert report.execution_trace == granite_result.execution_trace


async def test_granite_quota_marks_it_unavailable_while_bob_succeeds(monkeypatch):
    from app.agents.granite import GraniteUnavailable

    async def fake_bob(prompt, files):
        return InvestigatorResult(investigator="bob", status="ok", root_cause="BOB")

    async def quota(*a, **kw):
        raise GraniteUnavailable(
            "the watsonx.ai token quota for this project is used up"
        )

    monkeypatch.setattr(panel.bob, "live_unavailable_reason", lambda: None)
    monkeypatch.setattr(panel.bob, "investigate", fake_bob)
    monkeypatch.setattr(panel.granite, "is_configured", lambda: True)
    monkeypatch.setattr(panel, "investigate_demo", quota)

    channel = EventChannel()
    bob_r, granite_r = await panel.investigate_demo_panel(
        ISSUE, FILES, "E", "f", channel
    )
    assert bob_r.ok
    assert granite_r.status == "unavailable" and "token quota" in granite_r.error
    assert panel.source_of([bob_r, granite_r]) == "bob"
    assert any(
        "Granite unavailable: the watsonx.ai token quota" in e.message
        for e in channel.log
    )


# ── Free-text answers when Bob's JSON wrapper is damaged ──────────────────────


class _TextAnswer(bob.BaseModel):
    answer: str


def _parse_text(last_message: str, *extra_lines: str):
    stdout = "\n".join([*extra_lines, _result_line(last_message)])
    return bob.parse_answer(stdout, "", 0, _TextAnswer, text_field="answer")


def test_text_answer_with_raw_newlines_is_accepted():
    answer = _parse_text('{"answer": "## Findings\nline two with a "quote""}')
    assert answer.status == "ok"
    assert answer.data.answer.startswith("## Findings\nline two")


def test_text_answer_followed_by_prose_is_accepted():
    answer = _parse_text('{"answer": "fine"}\n\nHope this helps!')
    assert answer.status == "ok" and answer.data.answer == "fine"


def test_plain_markdown_reply_is_used_as_the_answer():
    answer = _parse_text("## Scan findings\n\n1. Whisper fallback crashes")
    assert answer.status == "ok"
    assert answer.data.answer.startswith("## Scan findings")


def test_limit_output_is_never_passed_off_as_an_answer():
    answer = _parse_text(
        "Contents of file x.py: ...",
        json.dumps(
            {"type": "error", "message": "The task reached the maximum of 6 turns."}
        ),
    )
    assert answer.status == "limit"


def test_without_text_field_damaged_json_still_fails():
    stdout = _result_line("## not json at all")
    assert bob.parse_answer(stdout, "", 0, _TextAnswer).status == "error"


def test_damaged_reproducer_json_still_yields_the_test():
    from app.agents.reproducer import ReproTest, code_from, validate

    reply = '```json\n{"test_source": "from m import f\n\ndef test_f():\n    assert f("a") == 1\n", "explanation": "x"}\n```'
    stdout = _result_line(reply)
    answer = bob.parse_answer(stdout, "", 0, ReproTest, text_field="test_source")
    assert answer.status == "ok"
    assert validate(code_from(answer.data.test_source)) is None
