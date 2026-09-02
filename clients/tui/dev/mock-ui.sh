#!/usr/bin/env bash
# mock-ui.sh - run the real TUI against a replayed run, with no backend and no
# tokens. Development tooling only; nothing here ships.
#
# Usage:
#   mock-ui.sh [--fixture PATH] [--theme NAME] [--speed N] [--paused]
#              [--tmux COLSxROWS] [-- <extra mock-server args>]
#
#   mock-ui.sh                          # replay the bundled fixture, interactive
#   mock-ui.sh --speed 0                # everything at once, no delays
#   mock-ui.sh --theme light
#   mock-ui.sh --fixture ~/dev/vibesys-runs/<run>/logs/run-events.jsonl
#   mock-ui.sh --tmux 200x50            # detached, for capture-pane scripting
#
# Inside the TUI, /pause and /resume control the replay itself.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TUI="$(dirname "$HERE")"
SOCKET="${VS_MOCK_SOCKET:-/tmp/vs-mock-$$.sock}"
SESSION="${VS_MOCK_SESSION:-vsmock}"

fixture=""
theme=""
tmux_size=""
mock_args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --fixture) fixture="$2"; shift 2 ;;
    --theme)   theme="$2";   shift 2 ;;
    --tmux)    tmux_size="$2"; shift 2 ;;
    --speed)   mock_args+=(--speed "$2"); shift 2 ;;
    --max-gap) mock_args+=(--max-gap "$2"); shift 2 ;;
    --bootstrap) mock_args+=(--bootstrap "$2"); shift 2 ;;
    --paused|--verbose) mock_args+=("$1"); shift ;;
    --) shift; mock_args+=("$@"); break ;;
    *) echo "mock-ui: unknown flag $1" >&2; exit 2 ;;
  esac
done
[ -n "$fixture" ] && mock_args+=(--fixture "$fixture")

if [ ! -f "$TUI/dist/index.js" ]; then
  echo "mock-ui: TUI build missing. Run: pnpm --dir $TUI build" >&2
  exit 1
fi

# The server's log goes to a file rather than a pipe: in --tmux mode nothing is
# left attached to read it, and an inherited pipe would hold the caller's stdout
# open. The path carries a pid so a failed start never reports an older run's
# log as its own.
if [ -n "${VS_MOCK_LOG:-}" ]; then
  MOCK_LOG="$VS_MOCK_LOG"; OWNED_LOG=""
else
  MOCK_LOG="${TMPDIR:-/tmp}/vs-mock-$$.log"; OWNED_LOG="$MOCK_LOG"
fi
: >"$MOCK_LOG"

# `${a[@]+"${a[@]}"}` rather than `"${a[@]}"`: under `set -u`, bash 3.2, which
# is what macOS ships, treats an empty array as unbound, so running with no
# flags at all would abort here.
quoted_args=""
for arg in ${mock_args[@]+"${mock_args[@]}"}; do
  quoted_args="$quoted_args $(printf '%q' "$arg")"
done

# Both modes run the server and the client under one process, so the server can
# never outlive the thing it exists to feed. In --tmux mode that one process is
# the pane's shell, which tmux signals on kill-session; previously the server
# was started out here and disowned, so killing the session left it, its socket,
# and its log behind.
RUNNER="${TMPDIR:-/tmp}/vs-mock-run-$$.sh"
{
  echo '#!/usr/bin/env bash'
  echo 'set -uo pipefail'
  printf 'SOCKET=%q\n' "$SOCKET"
  printf 'SELF=%q\n' "$RUNNER"
  printf 'MOCK_LOG=%q\n' "$MOCK_LOG"
  printf 'export VS_MOCK_OWNED_LOG=%q\n' "$OWNED_LOG"
  printf 'bun %q --socket "$SOCKET"%s >"$MOCK_LOG" 2>&1 &\n' "$HERE/mock-server.ts" "$quoted_args"
  echo 'SERVER_PID=$!'
  echo 'for _ in $(seq 1 100); do [ -S "$SOCKET" ] && break; sleep 0.05; done'
  echo 'if [ ! -S "$SOCKET" ]; then'
  echo '  echo "mock-ui: mock server never bound $SOCKET; log follows" >&2'
  echo '  cat "$MOCK_LOG" >&2'
  # The log has just been printed, so a log this run generated is spent. One the
  # caller named through VS_MOCK_LOG is theirs and stays.
  echo '  kill "$SERVER_PID" 2>/dev/null'
  echo '  rm -f "$SOCKET" "$SELF" ${VS_MOCK_OWNED_LOG:+"$VS_MOCK_OWNED_LOG"}'
  echo '  exit 1'
  echo 'fi'
  printf 'export VIBESYS_CONTROL_SOCKET="$SOCKET"\n'
  [ -n "$theme" ] && printf 'export VIBESYS_THEME=%q\n' "$theme"
  # Unlink the runner while it still runs: the inode stays open, so nothing is
  # left behind once exec discards this shell.
  echo 'rm -f "$SELF"'
  # `exec`, not a foreground child. A bash parent waiting on the client cannot
  # act on SIGHUP until that child returns, and the client does not return, so
  # `tmux kill-session` orphaned the whole tree. Replacing the shell makes the
  # client the session's own process, and the server exits on its own once the
  # subscription closes.
  printf 'exec bun %q\n' "$TUI/dist/index.js"
} >"$RUNNER"
chmod +x "$RUNNER"

# Nothing below may report success it did not achieve. The script runs without
# `set -e`, so every launch step is checked explicitly.
launch_failed() {
  echo "mock-ui: $1" >&2
  [ -s "$MOCK_LOG" ] && cat "$MOCK_LOG" >&2
  tmux kill-session -t "$SESSION" 2>/dev/null
  rm -f "$RUNNER" "$SOCKET" ${OWNED_LOG:+"$OWNED_LOG"}
  exit 1
}

if [ -n "$tmux_size" ]; then
  cols="${tmux_size%x*}"; rows="${tmux_size#*x}"
  tmux kill-session -t "$SESSION" 2>/dev/null
  # The runner is the session's own process, not something typed into a shell
  # inside it. With send-keys the pane's children survived `kill-session` and
  # the replay outlived the window it was drawing to.
  tmux new-session -d -s "$SESSION" -x "$cols" -y "$rows" "$RUNNER" ||
    launch_failed "tmux could not start session '$SESSION'"
  # `new-session` forks the runner, so it returns 0 even when the runner dies
  # immediately, on an unreadable fixture for instance. The session ends with
  # its command, so waiting for the socket while watching the session is what
  # separates a slow start from a failed one.
  for _ in $(seq 1 100); do
    [ -S "$SOCKET" ] && break
    tmux has-session -t "$SESSION" 2>/dev/null || break
    sleep 0.05
  done
  tmux has-session -t "$SESSION" 2>/dev/null ||
    launch_failed "detached session '$SESSION' exited during startup"
  [ -S "$SOCKET" ] ||
    launch_failed "mock server never bound $SOCKET in the detached session"
  tmux set-option -t "$SESSION" window-size manual 2>/dev/null
  echo "mock-ui: session '$SESSION' at ${cols}x${rows}, socket $SOCKET, log $MOCK_LOG"
  echo "mock-ui: tmux capture-pane -t $SESSION -p       # read a frame"
  echo "mock-ui: tmux send-keys -t $SESSION Enter       # send keys"
  echo "mock-ui: tmux kill-session -t $SESSION          # stop, and the server with it"
  exit 0
fi

exec "$RUNNER"
