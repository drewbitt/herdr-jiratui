"""A thin JiraTUI subclass; all Jira behavior comes from the pinned dependency."""

import argparse
import asyncio
import json
import os
import sys
from importlib.resources import files

from jiratui.app import JiraApp
from jiratui.config import ApplicationConfiguration
from jiratui.utils.adf import convert_adf_to_markdown
from jiratui.utils.urls import build_external_url_for_issue
from jiratui.widgets.screen import MainScreen
from rich.text import Text
from textual import on, work
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label, OptionList


async def herdr(*args: str) -> dict:
    """Use argv and the inherited session; never include prompt text in errors."""
    if os.environ.get("HERDR_ENV") != "1":
        raise RuntimeError("Open JiraTUI inside a Herdr pane to delegate issues.")
    try:
        process = await asyncio.create_subprocess_exec(
            os.environ.get("HERDR_BIN_PATH", "herdr"),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        raise RuntimeError("Could not run the Herdr CLI.") from error
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    except TimeoutError as error:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise RuntimeError(
            "Herdr timed out. Check the target agent before trying again; "
            "a prompt may have arrived."
        ) from error
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    try:
        response = json.loads(stdout or stderr)
    except ValueError as error:
        raise RuntimeError("Herdr did not return a valid response.") from error
    if (
        not isinstance(response, dict)
        or process.returncode
        or response.get("error")
        or not isinstance(response.get("result"), dict)
    ):
        raise RuntimeError("Herdr could not complete the command. Check the agent and session.")
    return response["result"]


class AgentPicker(ModalScreen[str | None]):
    DEFAULT_CSS = """
    AgentPicker { align: center middle; }
    AgentPicker > Vertical {
        width: 80%; max-width: 100; height: auto; max-height: 80%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    AgentPicker OptionList { height: auto; max-height: 20; margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, key: str, agents: list[dict]):
        super().__init__()
        self.key, self.agents = key, agents

    def compose(self):
        with Vertical():
            yield Label(Text(f"Delegate {self.key} — Enter sends the issue; Esc cancels"))
            yield OptionList(
                *[
                    Text(
                        f"{agent.get('display_agent') or agent['agent']}"
                        f" · {agent['pane_id']} · {agent['agent_status']} · {agent.get('cwd', '')}"
                    )
                    for agent in self.agents
                ]
            )

    @on(OptionList.OptionSelected)
    def select_agent(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.agents[event.option_index]["pane_id"])

    def action_cancel(self) -> None:
        self.dismiss(None)


class HerdrJiraApp(JiraApp):
    # Textual resolves inherited relative CSS paths against the subclass's module.
    CSS_PATH = str(files("jiratui").joinpath(JiraApp.CSS_PATH))
    BINDINGS = [Binding("alt+ctrl+d", "delegate", "Delegate", priority=True)]
    _delegating = reactive(False, bindings=True)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action != "delegate":
            return super().check_action(action, parameters)
        if not isinstance(self.screen, MainScreen):
            return False
        if self._delegating or not self.screen.search_results_table.current_work_item_key:
            return None
        return True

    @on(DataTable.RowHighlighted)
    def refresh_delegate_binding(self) -> None:
        self.refresh_bindings()

    @work(group="delegate")
    async def action_delegate(self) -> None:
        if self._delegating or not isinstance(self.screen, MainScreen):
            return
        key = self.screen.search_results_table.current_work_item_key
        if not key:
            self.notify("Select an issue first.", severity="warning")
            return
        self._delegating = True
        try:
            result = await herdr("agent", "list")
            agents = [
                agent
                for agent in result.get("agents", [])
                if agent.get("pane_id")
                and agent["pane_id"] != os.environ.get("HERDR_PANE_ID")
                and agent.get("agent")
                and agent.get("agent_status") in {"idle", "done"}
            ]
            if not agents:
                self.notify("No ready Herdr agents. Start an agent or wait for one to finish.")
                return
            target = await self.push_screen_wait(AgentPicker(key, agents))
            if target is None:
                return
            response = await self.api.get_issue(key, fields=["summary", "description"])
            if not response.success or not response.result or not response.result.issues:
                self.notify("Could not retrieve the issue from Jira.", severity="error")
                return
            issue = response.result.issues[0]
            description = issue.description or ""
            if isinstance(description, dict):
                description = convert_adf_to_markdown(description)
            prompt = (
                f"Work on Jira issue {issue.key}: {issue.summary}\n"
                f"Link: {build_external_url_for_issue(issue.key)}\n\n"
                f"Description:\n{description}"
            )
            # Recheck readiness after the picker and Jira request; the target may have changed.
            current = await herdr("agent", "get", target)
            agent = current.get("agent") or {}
            if agent.get("agent_status") not in {"idle", "done"}:
                self.notify(
                    "That agent is no longer ready. Choose another agent.", severity="warning"
                )
                return
            await herdr("agent", "prompt", target, prompt)
            self.notify(f"Sent {key} to {target}.")
        except RuntimeError as error:
            self.notify(str(error), severity="error")
        finally:
            self._delegating = False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JiraTUI with Ctrl+Alt+D to delegate the selected issue to a Herdr agent."
    )
    parser.parse_args()
    try:
        settings = ApplicationConfiguration()
    except (ValueError, OSError):
        print(
            "JiraTUI configuration is missing or invalid. Run: bin/run jiratui configure create",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    HerdrJiraApp(settings).run()
