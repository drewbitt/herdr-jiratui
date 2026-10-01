# herdr-jiratui

[JiraTUI](https://github.com/whyisdifficult/jiratui) in a Herdr tab, with an action to send issues to your agents.

![JiraTUI in a Herdr panel](docs/screenshot.png)

The plugin launches JiraTUI with an agent picker. JiraTUI handles the UI, credentials, and Jira API; the plugin sends the selected issue's key, summary, link, and description through `herdr agent prompt`.

## Use

Requires Herdr 0.9.3+ and [uv](https://docs.astral.sh/uv/). uv installs the project's Python version and locked dependencies when needed.

```sh
uvx jiratui configure create  # skip if JiraTUI is already configured
herdr plugin install drewbitt/herdr-jiratui
```

Add a launch shortcut to `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+shift+j"
type = "plugin_action"
command = "drewbitt.jiratui.open"
description = "JiraTUI: open"
```

Run `herdr server reload-config`, then press **your Herdr prefix, then Shift+J** to open JiraTUI in a new tab. You can also launch it without a keybinding:

```sh
herdr plugin action invoke drewbitt.jiratui.open
```

Use the plugin launcher for delegation. The regular `jiratui` binary runs upstream JiraTUI without the agent picker. The plugin uses your existing JiraTUI configuration, including `JIRA_TUI_CONFIG_FILE`, and opens a full-width tab to leave room for its search controls.

Select an issue and press **Ctrl+Alt+D** (Ctrl+Option+D on macOS). Use Up/Down to choose an idle or done agent, Enter to send, or Esc to cancel. If Herdr already binds Ctrl+Alt+D, change that binding so the panel receives it.

## Develop

```sh
uv sync --locked
herdr plugin link "$PWD"
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

JiraTUI is pinned in `pyproject.toml` because we use its internal application and selection interfaces. Test upgrades before changing that pin. Tests cover the terminal shortcut, issue handoff, agent readiness, and failed delivery using fake Jira responses and a fake Herdr executable.

The Renovate config schedules dependency and lock-file updates for the 1st and 15th. Updates need review; JiraTUI upgrades get their own PR. CI tests Python 3.12 and the version in `.python-version`, then builds the package.

The screenshot uses fictional issues in a temporary Herdr session.
