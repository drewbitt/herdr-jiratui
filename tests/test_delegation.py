import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from jiratui.config import ApplicationConfiguration
from jiratui.models import IssueStatus, IssueType, JiraIssue, JiraIssueSearchResponse
from jiratui.widgets.screen import MainScreen
from textual._xterm_parser import XTermParser
from textual.events import Key
from textual.widgets import OptionList

from herdr_jiratui import AgentPicker, HerdrJiraApp, herdr

KEY = "PROJ-1"
SUMMARY = "Preserve quotes, $(echo test), and `backticks`"
DESCRIPTION = {
    "type": "doc",
    "version": 1,
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "First line."}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Second line."}]},
    ],
}
ISSUE = {
    "id": "10001",
    "key": KEY,
    "fields": {
        "summary": SUMMARY,
        "description": DESCRIPTION,
        "status": {"id": "1", "name": "To Do", "statusCategory": {"key": "new"}},
        "issuetype": {"id": "1", "name": "Task", "subtask": False},
        "project": {"id": "1", "key": "PROJ", "name": "Test"},
    },
}


@pytest.fixture
def fake_herdr(tmp_path, monkeypatch):
    executable = tmp_path / "herdr"
    executable.write_text(
        f"#!{sys.executable}\n"
        """import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
mode = os.environ.get('FAKE_MODE', '')
if mode == 'timeout':
    time.sleep(60)
if mode == 'malformed':
    print('not json')
    sys.exit(0)
if args[:2] == ['agent', 'list']:
    agents = [
        {'pane_id': 'w1:p1', 'agent': 'codex', 'agent_status': 'idle', 'cwd': '/self'},
        {'pane_id': 'w1:p2', 'agent': 'claude', 'agent_status': 'done', 'cwd': '/repo with spaces'},
        {'pane_id': 'w1:p3', 'agent': 'codex', 'agent_status': 'working', 'cwd': '/busy'},
        {'pane_id': 'w1:p4', 'agent': 'claude', 'agent_status': 'blocked', 'cwd': '/blocked'},
        {'pane_id': 'w1:p5', 'agent_status': 'unknown'},
    ] if mode != 'empty' else []
    result = {'agents': agents, 'type': 'agent_list'}
elif args[:2] == ['agent', 'get']:
    result = {'agent': {'pane_id': args[2], 'agent': 'claude',
                        'agent_status': os.environ.get('FAKE_STATE', 'done')}}
elif args[:2] == ['agent', 'prompt']:
    if mode == 'error':
        print(json.dumps({'error': {'code': 'agent_blocked', 'message': args[-1]}}),
              file=sys.stderr)
        sys.exit(1)
    result = {'type': 'agent_prompt'}
else:
    result = {}
print(json.dumps({'id': 'test', 'result': result}))
"""
    )
    executable.chmod(0o755)
    log = tmp_path / "commands.jsonl"
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_BIN_PATH", str(executable))
    monkeypatch.setenv("HERDR_PANE_ID", "w1:p1")
    monkeypatch.setenv("FAKE_LOG", str(log))
    return log


def commands(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


@pytest.fixture
def app(tmp_path, monkeypatch, fake_herdr):
    config = tmp_path / "config.yaml"
    config.write_text(
        "jira_api_username: test@example.test\n"
        "jira_api_token: test-only\n"
        "jira_api_base_url: https://jira.example.test\n"
        "on_start_up_only_fetch_projects: true\n"
        "tui_title_include_jira_server_title: false\n"
        "search_on_startup: false\n"
        "enable_recent_history: false\n"
        "enable_goto: false\n"
        "log_file: null\n"
    )
    monkeypatch.setenv("JIRA_TUI_CONFIG_FILE", str(config))
    return HerdrJiraApp(ApplicationConfiguration())


async def select_issue(app, pilot):
    screen = app.screen
    assert isinstance(screen, MainScreen)
    screen.search_results_table.search_results = JiraIssueSearchResponse(
        issues=[
            JiraIssue(
                id="10001",
                key=KEY,
                summary=SUMMARY,
                status=IssueStatus(id="1", name="To Do"),
                issue_type=IssueType(id="1", name="Task"),
            )
        ]
    )
    screen.search_results_table.focus()
    await pilot.pause()
    assert screen.search_results_table.current_work_item_key == KEY


async def wait_for_picker(app, pilot):
    async def wait():
        while not isinstance(app.screen, AgentPicker):
            await pilot.pause(0.02)

    await asyncio.wait_for(wait(), timeout=5)


@pytest.fixture(autouse=True)
def offline_startup():
    # Preserve the real upstream screen/layout and table selection behavior.
    with (
        patch.object(MainScreen, "fetch_projects", new=AsyncMock()),
        patch.object(MainScreen, "fetch_issue", new=AsyncMock()),
    ):
        yield


@pytest.fixture
def jira():
    # All HTTP is intercepted; unexpected requests fail instead of reaching a real site.
    with respx.mock(assert_all_called=False) as router:
        route = router.get(f"https://jira.example.test/rest/api/3/issue/{KEY}").mock(
            return_value=httpx.Response(200, json=ISSUE)
        )
        yield route


async def test_selected_issue_to_ready_agent(app, fake_herdr, jira):
    async with app.run_test(size=(120, 40)) as pilot:
        await select_issue(app, pilot)
        await pilot.press("alt+ctrl+d")
        await wait_for_picker(app, pilot)
        assert isinstance(app.screen, AgentPicker)
        assert app.screen.query_one(OptionList).option_count == 1
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        sent = commands(fake_herdr)[-1]
        assert sent[:3] == ["agent", "prompt", "w1:p2"]
        assert SUMMARY in sent[3]
        assert "https://jira.example.test/browse/PROJ-1" in sent[3]
        assert "First line.\n\nSecond line." in sent[3]
        assert jira.call_count == 1
        assert isinstance(app.screen, MainScreen)


async def test_cancel_keeps_jiratui_and_sends_nothing(app, fake_herdr, jira):
    async with app.run_test(size=(120, 40)) as pilot:
        await select_issue(app, pilot)
        await pilot.press("alt+ctrl+d")
        await wait_for_picker(app, pilot)
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        assert commands(fake_herdr) == [["agent", "list"]]
        assert jira.call_count == 0
        assert isinstance(app.screen, MainScreen)


async def test_terminal_encoded_shortcut(app, fake_herdr):
    key = next(event.key for event in XTermParser().feed("\x1b[100;7u") if isinstance(event, Key))
    async with app.run_test(size=(120, 40)) as pilot:
        await select_issue(app, pilot)
        await pilot.press(key)
        await wait_for_picker(app, pilot)
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        assert commands(fake_herdr) == [["agent", "list"]]


async def test_repeated_shortcut_does_not_cancel_picker(app, fake_herdr, jira):
    async with app.run_test(size=(120, 40)) as pilot:
        await select_issue(app, pilot)
        await pilot.press("alt+ctrl+d", "alt+ctrl+d")
        await wait_for_picker(app, pilot)
        await pilot.press("alt+ctrl+d")
        await pilot.pause()
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert len([c for c in commands(fake_herdr) if c[:2] == ["agent", "list"]]) == 1
        assert len([c for c in commands(fake_herdr) if c[:2] == ["agent", "prompt"]]) == 1


async def test_no_selection_does_not_call_herdr(app, fake_herdr):
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("alt+ctrl+d")
        await app.workers.wait_for_complete()
        assert commands(fake_herdr) == []


async def test_no_ready_agents(app, fake_herdr, jira, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "empty")
    async with app.run_test(size=(120, 40)) as pilot:
        await select_issue(app, pilot)
        await pilot.press("alt+ctrl+d")
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, MainScreen)
        assert jira.call_count == 0
        assert commands(fake_herdr) == [["agent", "list"]]


@pytest.mark.parametrize("state", ["working", "blocked", "unknown"])
async def test_agent_becomes_unready(app, fake_herdr, jira, monkeypatch, state):
    monkeypatch.setenv("FAKE_STATE", state)
    async with app.run_test(size=(120, 40)) as pilot:
        await select_issue(app, pilot)
        await pilot.press("alt+ctrl+d")
        await wait_for_picker(app, pilot)
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert commands(fake_herdr) == [["agent", "list"], ["agent", "get", "w1:p2"]]


async def test_jira_failure_does_not_send(app, fake_herdr, jira):
    jira.mock(return_value=httpx.Response(403, json={"errorMessages": ["Forbidden"]}))
    async with app.run_test(size=(120, 40)) as pilot:
        await select_issue(app, pilot)
        await pilot.press("alt+ctrl+d")
        await wait_for_picker(app, pilot)
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert commands(fake_herdr) == [["agent", "list"]]


async def test_failed_submission_is_not_retried(app, fake_herdr, jira, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "error")
    async with app.run_test(size=(120, 40)) as pilot:
        await select_issue(app, pilot)
        await pilot.press("alt+ctrl+d")
        await wait_for_picker(app, pilot)
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert len([c for c in commands(fake_herdr) if c[:2] == ["agent", "prompt"]]) == 1
        assert isinstance(app.screen, MainScreen)


async def test_herdr_errors_do_not_expose_prompt(fake_herdr, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "error")
    with pytest.raises(RuntimeError) as error:
        await herdr("agent", "prompt", "w1:p2", "sensitive issue description")
    assert "sensitive" not in str(error.value)


async def test_malformed_herdr_response(fake_herdr, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "malformed")
    with pytest.raises(RuntimeError, match="valid response"):
        await herdr("agent", "list")


async def test_outside_herdr_does_not_run_command(fake_herdr, monkeypatch):
    monkeypatch.delenv("HERDR_ENV")
    with pytest.raises(RuntimeError, match="inside a Herdr pane"):
        await herdr("agent", "list")
    assert commands(fake_herdr) == []


async def test_timeout_is_uncertain_and_not_retried(fake_herdr, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "timeout")
    original = asyncio.wait_for

    async def short_timeout(awaitable, timeout):
        return await original(awaitable, timeout=0.5)

    monkeypatch.setattr(asyncio, "wait_for", short_timeout)
    with pytest.raises(RuntimeError, match="a prompt may have arrived"):
        await herdr("agent", "prompt", "w1:p2", "test")
    assert len(commands(fake_herdr)) == 1
