# herdr-jiratui

[JiraTUI](https://github.com/whyisdifficult/jiratui) in Herdr, with one extra
shortcut: Ctrl+Alt+D sends the selected issue to a running agent.

![JiraTUI in a Herdr panel](docs/screenshot.png)

JiraTUI handles the UI, credentials, and Jira API. This plugin adds an agent
picker and sends the issue's key, summary, link, and description through
`herdr agent prompt`.

## Use

Requires Herdr 0.9.3+ and [uv](https://docs.astral.sh/uv/). uv installs Python
3.14 and the locked dependencies when needed.

```sh
uvx jiratui@1.15.0 configure create  # skip if JiraTUI is already configured
herdr plugin install drewbitt/herdr-jiratui
herdr plugin action invoke drewbitt.jiratui.open
```

Your existing JiraTUI config works, including `JIRA_TUI_CONFIG_FILE`.
Each open action creates a tab. Select an issue, press Ctrl+Alt+D, then Enter
to send it. Esc cancels. The picker shows agents that are idle or done.

## Develop

```sh
uv sync --locked
herdr plugin link "$PWD"
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

JiraTUI is pinned to 1.15.0 because we use its internal application and selection
interfaces. Test upgrades before changing that pin. Tests cover the terminal
shortcut, issue handoff, agent readiness, and failed delivery using fake Jira
responses and a fake Herdr executable.

The screenshot uses fictional issues in a temporary Herdr session.
