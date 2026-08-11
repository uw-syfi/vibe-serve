# VibeSys TUI

Terminal client and launcher for VibeSys.

```bash
npm install -g @vibesys/tui
vs --help
```

The package installs `vs` and `vibesys` as aliases for the same launcher. The
launcher starts the Python VibeSys backend with `python -m vibesys --headless`
from the current directory and then attaches the OpenTUI client. The launcher
passes run arguments through unchanged, including an explicit legacy
`--runs-dir`; it does not open a separate setup form. Install the Python
`vibesys` package in the Python environment you want to use, or set
`VIBESYS_PYTHON` to that Python executable.

## Operator interface

Enter ordinary text to ask the supervision backend about the current run. The
available slash commands are:

| Command | Behavior |
| --- | --- |
| `/help` | Show commands and planned controls. |
| `/chat` | Open a read-only chat about the run; `/chat <question>` asks immediately. |
| | Slash commands work inside the chat too, and do the same thing as in the main input. |
| `/pause` | Pause after the current agent call finishes. |
| `/resume` | Resume a paused run. |
| `/steer <message>` | Queue an instruction that is appended to the next agent invocation's prompt. |
| `/history` | List rounds with agent-active elapsed time. |
| `/perf` | Plot the recorded performance metric by round. |
| `/theme` | List themes; `/theme <name>` switches immediately. |

### Experiment chat

The chat is answered by a coding agent scoped to the run, using the backend,
provider, and model from `[agent]` in `agent.toml`, with conversation state
carried across turns through `_vibesys_chat/conversation.jsonl` in the
workspace. The agent handler exists only while the run context does, so a
question asked during startup or after the run finishes has no agent to reach;
the reply says so and falls back to a read-only keyword summary of the recorded
events rather than presenting that summary as the answer.

Text typed in the chat that starts with `/` is parsed as a slash command
through the same path as the main input, so `/history` there does exactly what
`/history` does anywhere else. Anything else is a question for the agent, and a
question containing a slash mid-sentence is still a question.

The footer shows keyboard navigation. `[` and `]` select rounds, Tab and
Shift+Tab select agents, Page Up/Page Down scroll the transcript, Ctrl+T expands
todos, Ctrl+P expands the latest prompt in the current selection, Ctrl+L returns
to the live view, and Ctrl+C exits. Commands listed under "Planned" in `/help`
are not accepted yet.

The launcher retains terminal results until the operator exits. If the backend
fails to start, its log tail is printed before the temporary session directory
is removed. Requests and subscription setup have bounded timeouts; malformed or
incompatible protocol messages are shown as errors instead of crashing a socket
callback.

## Themes

Four light/dark pairs ship: `dark` (default) / `light`, `solarized-dark` /
`solarized-light`, `catppuccin-mocha` / `catppuccin-latte`, and
`high-contrast-dark` / `high-contrast-light`. Selecting `dark` reproduces the
appearance the client had before themes existed: conversation cards, the
tool-call bands, and the Markdown palette are pinned to the original literals.
Four near-duplicate status shades were deliberately folded into the role they
belong to — a completed todo now uses the same green as an active agent phase,
completed phases and prompt-disclosure hints use the same blue as the detail
overlay, round labels use the same body-text color as card content, and the
chat panel's inner border matches its outer one. `theme.test.ts` pins all of
this so the baseline cannot drift.

Pick one with `--theme <name>`; launches without the flag use `dark`. The
launcher passes the selected name to the client through `VIBESYS_THEME`.
Inside a session, `/theme` lists the themes and `/theme <name>` re-themes every
view in place.

`ui/theme.ts` is the only module holding color literals. A theme declares
semantic roles — `canvas`, `surface`, `elevatedSurface`, `selectedSurface`;
`textPrimary`, `textMuted`, `textSubtle`, `textStrong`; `border`,
`borderStrong`, `borderFocus`; `accent`, `info`; `success`, `warning`, `error`;
per-role conversation card colors; and Markdown/code colors. Views ask for a
role and never for a color.

Adding a theme means adding one `ThemeSpec`: a semantic core plus one accent
per conversation role. Card fills, labels, body text, the tool-call band, and
the Markdown palette are derived from that core, and each derived foreground is
pushed toward the nearest extreme until it clears the theme's `minContrast`
against the surface it actually sits on. The `dark` theme additionally pins its
derived values to the original literals so the baseline is byte-identical.
Status meaning never depends on color: agent phases carry a marker glyph and
the spelled-out status, todos carry a per-status marker, and only the running
round shows elapsed time.

## Architecture

The Python backend owns the validated, append-only event contract and serves it
as JSONL over a private Unix socket. `src/generated/` is generated from those
Pydantic models. The TypeScript client owns framing and request correlation,
`session-controller.ts` owns effects, `session-model.ts` and `run-map.ts` reduce
events into presentation state, and `ui/` owns OpenTUI rendering and input.

Conversation state retains at most 1,000 semantic entries. Rendering is keyed
by entry identity: state-only updates reuse existing cards, streamed tail
updates replace only the final card, and a full rebuild is reserved for filter
or history-window changes. Typed tool calls use stable call IDs so parallel
results return to the correct card; old event logs without IDs use a documented
FIFO-by-tool fallback.

## Development

From the repository root:

```bash
pnpm install --frozen-lockfile
pnpm --dir clients/tui generate:protocol
pnpm --dir clients/tui check
pnpm --dir clients/tui test
pnpm --dir clients/tui build
pnpm check:ts
uv run pytest tests/test_tui.py tests/agents/test_callbacks.py tests/render/test_sink.py
```

After changing Python protocol models, regenerate both files in
`src/generated/` and review their diff. The test suite covers reducer behavior,
OpenTUI frames and navigation, launcher cleanup, socket fragmentation and
timeouts, replay/live delivery, and the Python supervision service.
