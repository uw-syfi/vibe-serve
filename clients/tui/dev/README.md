# TUI development harness

Runs the real TUI against a recorded run, with no Python backend, no agent, and
no tokens. Use it to iterate on rendering, layout, themes, and keybindings.

Nothing here ships. `tsconfig.json` sets `rootDir: "src"`, so `dev/` is never
compiled into `dist`, and `package.json` publishes only `dist`. No file on the
shipping path imports anything in this directory. `tsconfig.check.json` widens
the root to cover `dev/**`, so `pnpm check:clients` typechecks this code; that
pass emits nothing, so it cannot put any of it on the shipping path.

That widening has one cost: with `dev/` in the check program, a `src/` file
importing this directory typechecks cleanly, where before it did not. The build
still rejects it (`rootDir: "src"`, TS6059), but the rule belongs where the other
layering rules live, so `.dependency-cruiser.cjs` states it directly as
`shipping-path-does-not-depend-on-dev-harness`.

## Why it needs no product code

The TUI is already a separate process that connects to whatever Unix socket
`VIBESYS_CONTROL_SOCKET` names (`src/index.ts`). `mock-server.ts` speaks the
server side of that protocol and replays a recorded `run-events.jsonl`. The
binary under test is the unmodified `dist/index.js`.

## Usage

```bash
pnpm --dir clients/tui build          # once, and after any src change
clients/tui/dev/mock-ui.sh            # replay the bundled fixture
```

| Flag | Effect |
| --- | --- |
| `--speed N` | Wall-clock divisor from the recorded timestamps. `0` delivers everything at once. Default `8`. |
| `--max-gap MS` | Caps any single gap, so a four-minute agent turn does not stall the replay. Default `400`. |
| `--paused` | Hold before the first live event; `/resume` in the TUI starts it. |
| `--bootstrap N` | Deliver the first N events instantly as recorded history, then stream the rest. |
| `--theme NAME` | Any theme in `src/ui/theme.ts`. |
| `--fixture PATH` | Any `run-events.jsonl`, plain or `.gz`, including one of your own runs. |
| `--tmux COLSxROWS` | Run detached in tmux for scripted frame capture. |

`/pause` and `/resume` inside the TUI control the replay itself, so you can stop
on a frame and inspect it.

There is deliberately no loop flag. Restarting the fixture would replay
`run_started` into a client that already folded the run to terminal, leaving it
with a running status, a terminal flag still set, and a second transcript
appended to the first. Looping needs a real reset of client state, which the
protocol has no message for, so the honest options were to remove it or to fake
a reconnect. Re-run the command instead.

### Scripted capture

```bash
clients/tui/dev/mock-ui.sh --tmux 200x50 --speed 0
tmux capture-pane -t vsmock -p        # plain text, for layout and wrapping
tmux capture-pane -t vsmock -e -p     # with escapes, for color and contrast
tmux send-keys -t vsmock Enter
tmux kill-session -t vsmock
```

### Replaying your own run

```bash
clients/tui/dev/mock-ui.sh --fixture ~/.vibesys/projects/<project>/runs/<run>/logs/run-events.jsonl
```

## Fixtures

Both are real captures, scrubbed of local usernames and home paths, committed
plain. Git zlib-compresses blobs, so 1.7MB of JSONL costs a packed repository
the same ~266KB either way; gzipping would only make the scrub unreviewable and
a hand-edit unreadable as a diff. They are deliberately not marked `-diff`:
that hides the scrub in review, which is the reason they are plain at all. If a
re-record ever makes the diff noise a real problem, mark them then.

| Fixture | Events | Why it exists |
| --- | --- | --- |
| `bad-cpp-round1.jsonl` | 403 | The only capture with a full lifecycle: `run_started` through `judge_result`, `benchmark_result`, `round_finished`, `run_finished`, plus four `chat` turns. |
| `queue-rs-payloads.jsonl` | 628 | Every one of its 105 `tool_result` events carries a typed `payload` (`kind: "command"`), and it has 214 `agent_execution_activity_changed` events. This is the one to use for tool-result rendering work. It ends in `run_interrupted`/`run_failed`, so it also exercises the error banner. |

Neither carries `todo_update` events, so the todo strip stays empty on both.

`bad-cpp` predates the current schema: its records omit `execution_id`,
`chat_thread_id`, and `diagnostic`, and every `tool_result` has `payload: null`.
`RunEvent` tolerates that by design, so it is the harness's one capture of a
client falling back to raw `content`.

That tolerance is checked rather than assumed. `tests/server/test_tui_dev_harness.py`
declares what each fixture guarantees: every line of every fixture must validate
as a `RunEvent`, and `queue-rs-payloads`, recorded on the current schema, must
additionally re-serialize to itself byte for byte, which is what catches a field
being added. `bad-cpp-round1` is declared legacy there and only has to validate.
A new fixture has to declare the same, or the directory check fails.

## Protocol notes

Relevant if you extend `mock-server.ts`:

- Newline-delimited JSON both directions, no handshake. The client says nothing
  on connect and correlates purely by `request_id`.
- Every response needs `protocol_version: 1`, `request_id`, and `ok`, or the
  client destroys the socket and fails every pending request.
- The TUI opens three connections to the one path: control, the event stream
  (one per `subscribe`), and one per `query.chat`. Connections are handled
  independently; none is closed early.
- On `subscribe`, order is `subscribed`, then a bootstrap `event_batch`, then
  live batches. `history_after_sequence: 0` tells the TUI it holds the whole
  history and suppresses backfill requests.
- The recorded `run-events.jsonl` line format is exactly the `RunEvent` that
  goes on the wire, so replay is just re-enveloping lines into `event_batch`.
- Response bodies are literals in `mock-responses.json`, keyed by request type,
  rather than inline in `mock-server.ts`. That is what lets
  `tests/server/test_tui_dev_harness.py` validate the exact bytes sent against
  the `Response` model. Runtime values go in through `withValues`, which
  refuses any key the file does not already carry.
