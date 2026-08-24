import type {Diagnostic, HypothesisEntry, RunEvent, RunSnapshot} from './protocol.js';
import {
  type AgentPhase,
  applyRunMapEvent,
  type RoundSummary,
  roundNumberFromLabel,
  visiblePhases as visibleRunMapPhases,
  visibleRoundNumber as visibleRunMapRoundNumber,
} from './run-map.js';
import {DEFAULT_THEME_NAME, THEME_NAMES, type ThemeName} from './ui/theme.js';

export interface SessionState {
  sequence: number;
  status: string;
  agentKind: string | null;
  roundLabel: string | null;
  outerLoop: string | null;
  rounds: RoundSummary[];
  /** Rounds the run intends to reach, from ``run_started``; null until it arrives. */
  maxRounds: number | null;
  phases: AgentPhase[];
  selectedRound: number | null;
  selectedAgentKind: string | null;
  /** Transcript entry the arrow keys are on, so a trace is readable without a mouse. */
  selectedEntryId: string | null;
  /** Todo row the arrow keys are on while the todo box holds focus. */
  selectedTodoIndex: number | null;
  /**
   * Which half of the round view the arrow keys act on. The round view is two
   * panes side by side, and both are navigable, so the keys have to belong to
   * one of them at a time.
   */
  roundFocus: RoundFocus;
  conversation: ConversationEntry[];
  overlay: OverlayPanel | null;
  chatOpen: boolean;
  chatConversation: ConversationEntry[];
  chatPending: boolean;
  chatTypedToolEvents: boolean;
  terminal: boolean;
  todoPhases: PhaseTodos[];
  todosExpanded: boolean;
  usage: UsageMeter | null;
  themeName: ThemeName;
  experimentLog: ExperimentLogState | null;
  hypothesisScope: HypothesisScope | null;
  layout: LayoutState;
  /**
   * Whether the terminal is wide enough to carry the docked chat beside the
   * log. Measured by the renderer and reported in, because where a question's
   * answer is displayed has to agree with what is actually on screen: too
   * narrow to dock and the chat is the modal it has always been.
   */
  chatDockFits: boolean;
  /** Non-null while the theme list is open as a keyboard selection. */
  themePicker: ThemePicker | null;
  /**
   * Set once a typed tool_call/tool_result event is seen. From then on the
   * legacy tool-channel text chunks (still present in event files recorded
   * by older backends) are ignored so tool turns never render twice.
   */
  typedToolEvents: boolean;
  /** Root-level error state, independent of the active transcript or log view. */
  errorBanner: ErrorBannerState | null;
}

export type ErrorSeverity = 'recoverable' | 'fatal';
export type ErrorScope =
  | 'configuration'
  | 'invocation'
  | 'phase'
  | 'run'
  | 'protocol'
  | 'request'
  | 'transport'
  | 'input';

export interface ErrorBannerState {
  title: string;
  /** Human-facing summary, shown before structured detail and hint. */
  message: string;
  detail: string | null;
  hint: string | null;
  diagnosticId: string | null;
  severity: ErrorSeverity;
  scope: ErrorScope;
  agentKind: string | null;
  roundLabel: string | null;
  invocationId: string | null;
  /** Equivalent reports are folded into one banner. */
  count: number;
}

/** The agent graph on the left, or the transcript on the right. */
export type RoundFocus = 'agents' | 'transcript';

export interface TodoItem {
  content: string;
  status: string;
}

/**
 * One agent phase's latest todo-list snapshot. Todos are keyed per
 * (round, agent) so concurrent or successive phases never clobber each
 * other's lists, mirroring how the conversation filter scopes entries.
 */
export interface PhaseTodos {
  agentKind: string | null;
  roundNumber: number | null;
  items: TodoItem[];
}

/**
 * The experiment log is open when this is non-null. Selection is held as a
 * hypothesis id rather than a row index so a refresh that inserts rows keeps
 * the operator on the same hypothesis.
 */
export interface ExperimentLogState {
  entries: HypothesisEntry[];
  selectedId: string | null;
  pending: boolean;
  error: string | null;
}

/**
 * The hypothesis whose rounds the operator opened. While this is set the
 * client shows the ordinary per-round trajectory, filtered to these rounds,
 * and the log table steps aside without losing its selection.
 */
export interface HypothesisScope {
  id: string;
  label: string;
  rounds: number[];
}

/**
 * A visualization command's output, rendered beside the transcript rather than
 * over it. Content is pre-rendered text so the pane stays agnostic about which
 * command produced it and a new command needs no new layout code.
 */
export type PaneView = 'perf';

export interface RightPane {
  view: PaneView;
  title: string;
  content: string;
  pending: boolean;
  error: string | null;
}

/**
 * Which column the pane keys act on. ``left`` is whatever holds the middle of
 * the row, the experiment log or the transcript; ``chat`` and ``right`` are the
 * panes beside it.
 */
export type PaneFocus = 'chat' | 'left' | 'right';

export interface LayoutState {
  /** null means no visualization pane: the left side has the rest of the row. */
  right: RightPane | null;
  focus: PaneFocus;
}

export interface ThemePicker {
  selected: ThemeName;
}

export interface UsageMeter {
  inputTokens: number;
  contextWindow: number | null;
  model: string | null;
}

export interface OverlayPanel {
  kind: 'detail' | 'help' | 'error';
  content: string;
}

export interface ConversationEntry {
  id: string;
  kind:
    | 'assistant'
    | 'user'
    | 'prompt'
    | 'analysis'
    | 'tool'
    | 'diagnostic'
    | 'subprocess'
    | 'status'
    | 'result';
  content: string;
  label?: string;
  tone?: 'normal' | 'success' | 'failure';
  agentKind?: string;
  roundLabel?: string;
  roundNumber?: number;
  turnId?: string;
  invocationId?: string;
  startsTurn?: boolean;
  toolCall?: string;
  toolResponse?: string;
  toolName?: string;
  toolCallId?: string;
}

export function initialSessionState(themeName: ThemeName = DEFAULT_THEME_NAME): SessionState {
  return {
    sequence: 0,
    status: 'connecting',
    agentKind: null,
    roundLabel: null,
    outerLoop: null,
    rounds: [],
    maxRounds: null,
    phases: [],
    selectedRound: null,
    selectedAgentKind: null,
    selectedEntryId: null,
    selectedTodoIndex: null,
    roundFocus: 'transcript',
    conversation: [],
    overlay: null,
    chatOpen: false,
    chatConversation: [],
    chatPending: false,
    chatTypedToolEvents: false,
    terminal: false,
    todoPhases: [],
    todosExpanded: false,
    usage: null,
    themeName,
    // The experiment log is the landing view: a run's history reads as a short
    // list of claims before it reads as a long list of rounds.
    experimentLog: {entries: [], selectedId: null, pending: true, error: null},
    hypothesisScope: null,
    layout: {right: null, focus: 'left'},
    // Docked until the renderer measures otherwise, so the landing view carries
    // the chat from the first frame rather than after a resize.
    chatDockFits: true,
    themePicker: null,
    typedToolEvents: false,
    errorBanner: null,
  };
}

/**
 * Rows are keyed by hypothesis id, which the server orders by first round and
 * never reshuffles. Selection therefore survives a refresh even when a new
 * hypothesis lands above the current one.
 */
export function entryKey(entry: HypothesisEntry, index: number): string {
  return entry.identified === false
    ? `${entry.hypothesis_id}#${entry.first_round}`
    : entry.hypothesis_id || `#${index}`;
}

/**
 * True when the table itself is on screen rather than a hypothesis trajectory.
 * The chat does not enter into it: docked it is part of this view, and as a
 * modal it floats over it, so asking a question never swaps the table for the
 * per-round transcript underneath.
 */
export function experimentLogVisible(state: SessionState): boolean {
  return state.experimentLog !== null && state.hypothesisScope === null;
}

/**
 * The chat is docked on the landing view: it is part of that view rather than a
 * dialog over it, so a question never hides the table it is about. Inside a
 * hypothesis, and in a terminal too narrow for two columns, it stays the modal
 * it was.
 */
export function chatDocked(state: SessionState): boolean {
  return state.chatDockFits && state.experimentLog !== null && state.hypothesisScope === null;
}

/** True when the docked chat is the thing on screen rather than the modal. */
export function chatPaneVisible(state: SessionState): boolean {
  return chatDocked(state) && !state.chatOpen;
}

export function chatPaneFocused(state: SessionState): boolean {
  return chatPaneVisible(state) && state.layout.focus === 'chat';
}

export function rightPaneFocused(state: SessionState): boolean {
  return state.layout.right !== null && state.layout.focus === 'right';
}

export function setChatDockFits(state: SessionState, fits: boolean): SessionState {
  if (state.chatDockFits === fits) return state;
  const layout =
    fits || state.layout.focus !== 'chat'
      ? state.layout
      : {...state.layout, focus: 'left' as const};
  return {...state, chatDockFits: fits, layout};
}

/**
 * What ``/chat`` does. Docked, the chat is already on screen, so opening it
 * means putting the pane keys on it rather than covering the table.
 */
export function openChat(state: SessionState): SessionState {
  if (chatDocked(state)) {
    return {...state, overlay: null, layout: {...state.layout, focus: 'chat'}};
  }
  return {...state, overlay: null, chatOpen: true};
}

export function openExperimentLog(state: SessionState): SessionState {
  const existing = state.experimentLog;
  return {
    ...state,
    overlay: null,
    chatOpen: false,
    hypothesisScope: null,
    selectedRound: null,
    selectedAgentKind: null,
    experimentLog: existing ?? {entries: [], selectedId: null, pending: true, error: null},
  };
}

export function setExperiments(state: SessionState, entries: HypothesisEntry[]): SessionState {
  const log = state.experimentLog;
  if (log === null) return state;
  const keys = entries.map(entryKey);
  // Keep the operator's row when it still exists; otherwise fall back to the
  // active hypothesis, then to the first row.
  const selectedId =
    log.selectedId !== null && keys.includes(log.selectedId)
      ? log.selectedId
      : (keys[entries.findIndex(entry => entry.active === true)] ?? keys[0] ?? null);
  return {
    ...state,
    experimentLog: {...log, entries, selectedId, pending: false, error: null},
  };
}

export function failExperiments(state: SessionState, error: string): SessionState {
  const log = state.experimentLog;
  if (log === null) return state;
  return {...state, experimentLog: {...log, pending: false, error}};
}

export function moveExperimentSelection(state: SessionState, delta: number): SessionState {
  const log = state.experimentLog;
  if (log === null || state.hypothesisScope !== null || log.entries.length === 0) return state;
  const keys = log.entries.map(entryKey);
  const current = log.selectedId === null ? -1 : keys.indexOf(log.selectedId);
  // With nothing selected the first key lands on the first row rather than
  // stepping past it: the table has a selection the moment it is used.
  if (current === -1) {
    return {...state, experimentLog: {...log, selectedId: keys[0] ?? null}};
  }
  const index = Math.min(keys.length - 1, Math.max(0, current + delta));
  return {...state, experimentLog: {...log, selectedId: keys[index] ?? null}};
}

/**
 * Opens the rounds behind the selected hypothesis. The log keeps its selection
 * so leaving the trajectory lands the operator back on the same row.
 */
export function enterExperimentDrilldown(state: SessionState): SessionState {
  const entry = selectedExperiment(state);
  if (entry === null || state.hypothesisScope !== null) return state;
  const rounds = scopeRounds(entry);
  if (rounds.length === 0) return state;
  return {
    ...state,
    overlay: null,
    // A visualization opened from the log belongs to the log. Carrying it into
    // the round view would leave the operator reading one view's answer beside
    // another view's transcript.
    layout: {right: null, focus: 'left'},
    hypothesisScope: {id: entry.hypothesis_id, label: hypothesisLabel(entry), rounds},
    // Land on the hypothesis's latest round rather than on "no round". The
    // round view is built around one round: the strip marks it, the agent graph
    // draws its phases, the transcript carries its turns. Leaving the round
    // unset drew an empty strip and an empty graph, and `[` walks back through
    // the earlier rounds from here anyway.
    selectedRound: rounds.at(-1) ?? null,
    selectedAgentKind: null,
    selectedEntryId: null,
  };
}

/** Leaves the trajectory and returns to the table with the selection intact. */
export function leaveExperimentDrilldown(state: SessionState): SessionState {
  if (state.hypothesisScope === null) return state;
  return {...state, hypothesisScope: null, selectedRound: null, selectedAgentKind: null};
}

/**
 * Opens the hypothesis that owns a round number, landing on that round rather
 * than on the whole trajectory. Returns null when no hypothesis claims it, so
 * the caller can report the round rather than silently doing nothing.
 */
export function enterExperimentRound(
  state: SessionState,
  roundNumber: number,
): SessionState | null {
  const entry = (state.experimentLog?.entries ?? []).find(candidate =>
    scopeRounds(candidate).includes(roundNumber),
  );
  if (entry === undefined) return null;
  const scoped: SessionState = {
    ...state,
    overlay: null,
    chatOpen: false,
    layout: {right: null, focus: 'left'},
    hypothesisScope: {
      id: entry.hypothesis_id,
      label: hypothesisLabel(entry),
      rounds: scopeRounds(entry),
    },
    selectedRound: roundNumber,
    selectedAgentKind: null,
    selectedEntryId: null,
    experimentLog:
      state.experimentLog === null
        ? null
        : {...state.experimentLog, selectedId: entryKeyFor(state.experimentLog.entries, entry)},
  };
  return scoped;
}

function entryKeyFor(entries: HypothesisEntry[], entry: HypothesisEntry): string | null {
  const index = entries.indexOf(entry);
  return index === -1 ? null : entryKey(entry, index);
}

function scopeRounds(entry: HypothesisEntry): number[] {
  const listed = (entry.rounds ?? []).map(round => round.round);
  if (listed.length > 0) return [...listed].sort((a, b) => a - b);
  // An active hypothesis whose first round has not finished has no round
  // records yet, but its opening round is still worth showing.
  return entry.first_round > 0 ? [entry.first_round] : [];
}

function hypothesisLabel(entry: HypothesisEntry): string {
  const range =
    entry.first_round === entry.last_round
      ? `r${entry.first_round}`
      : `r${entry.first_round}-${entry.last_round}`;
  return `${entry.hypothesis_id} · ${range}`;
}

export function selectedExperiment(state: SessionState): HypothesisEntry | null {
  const log = state.experimentLog;
  if (log === null || log.selectedId === null) return null;
  const index = log.entries.map(entryKey).indexOf(log.selectedId);
  return index === -1 ? null : (log.entries[index] ?? null);
}

export const PANE_TITLES: Record<PaneView, string> = {
  perf: 'Performance',
};

/**
 * Opens or retargets the right pane. A second visualization command replaces
 * the pane's contents rather than stacking, which is why the view is set here
 * rather than pushed onto anything.
 */
export function openPane(state: SessionState, view: PaneView): SessionState {
  const existing = state.layout.right;
  return {
    ...state,
    overlay: null,
    layout: {
      right: {
        view,
        title: PANE_TITLES[view],
        // Keep the old content while the new query is in flight only when the
        // pane is not changing view, so the pane never shows one command's
        // output under another command's title.
        content: existing !== null && existing.view === view ? existing.content : '',
        pending: true,
        error: null,
      },
      focus: 'right',
    },
  };
}

export function setPaneContent(state: SessionState, view: PaneView, content: string): SessionState {
  const right = state.layout.right;
  // A slower response for a pane the operator has since replaced or closed
  // must not overwrite what is on screen now.
  if (right === null || right.view !== view) return state;
  return {
    ...state,
    layout: {...state.layout, right: {...right, content, pending: false, error: null}},
  };
}

export function failPane(state: SessionState, view: PaneView, error: string): SessionState {
  const right = state.layout.right;
  if (right === null || right.view !== view) return state;
  return {...state, layout: {...state.layout, right: {...right, pending: false, error}}};
}

export function closePane(state: SessionState): SessionState {
  if (state.layout.right === null) return state;
  return {...state, layout: {right: null, focus: 'left'}};
}

/**
 * Moves the pane keys one column to the right, wrapping. Only the columns
 * actually on screen take part, so the operator never lands on a pane they
 * cannot see.
 */
export function cyclePaneFocus(state: SessionState): SessionState {
  const order = visiblePaneOrder(state);
  if (order.length < 2) return state;
  const next = order[(order.indexOf(state.layout.focus) + 1) % order.length] ?? 'left';
  return {...state, layout: {...state.layout, focus: next}};
}

/**
 * Focus has to name a column that is actually on screen. A pane can close, or a
 * view can change, while the keys still point at it, and every keystroke then
 * goes to a surface that is not there: the client looks frozen. Called on every
 * state change, so focus can never be left pointing at nothing.
 */
export function normalizeFocus(state: SessionState): SessionState {
  const order = visiblePaneOrder(state);
  if (order.includes(state.layout.focus)) return state;
  return {...state, layout: {...state.layout, focus: 'left'}};
}

/** Escape from a round view: close whatever is layered over it, all of it. */
export function closeOverlays(state: SessionState): SessionState {
  if (state.layout.right === null && !state.chatOpen && state.overlay === null) return state;
  return {
    ...state,
    overlay: null,
    chatOpen: false,
    layout: {right: null, focus: 'left'},
  };
}

export function focusPane(state: SessionState, focus: PaneFocus): SessionState {
  if (state.layout.focus === focus) return state;
  if (!visiblePaneOrder(state).includes(focus)) return state;
  return {...state, layout: {...state.layout, focus}};
}

function visiblePaneOrder(state: SessionState): PaneFocus[] {
  return [
    ...(chatPaneVisible(state) ? (['chat'] as const) : []),
    'left' as const,
    ...(state.layout.right !== null ? (['right'] as const) : []),
  ];
}

export function setTheme(state: SessionState, themeName: ThemeName): SessionState {
  // Applying the theme that is already active is still an answer to the
  // picker, so the picker closes either way.
  if (state.themeName === themeName) {
    return state.themePicker === null ? state : {...state, themePicker: null};
  }
  return {...state, themeName, overlay: null, themePicker: null};
}

/** Opens the theme list as a selection, starting on the active theme. */
export function openThemePicker(state: SessionState): SessionState {
  return {...state, overlay: null, themePicker: {selected: state.themeName}};
}

/**
 * Moves the highlighted theme. The list has ends rather than a cycle, so the
 * selection clamps instead of wrapping.
 */
export function moveThemeSelection(state: SessionState, delta: number): SessionState {
  const picker = state.themePicker;
  if (picker === null) return state;
  const current = THEME_NAMES.indexOf(picker.selected);
  const index = Math.min(THEME_NAMES.length - 1, Math.max(0, current + delta));
  const selected = THEME_NAMES[index];
  if (selected === undefined || selected === picker.selected) return state;
  return {...state, themePicker: {selected}};
}

/** Closes the picker, leaving the theme as it was when it opened. */
export function closeThemePicker(state: SessionState): SessionState {
  if (state.themePicker === null) return state;
  return {...state, themePicker: null};
}

export function applySnapshot(state: SessionState, snapshot: RunSnapshot): SessionState {
  return {
    ...state,
    status: snapshot.status,
    agentKind: snapshot.agent_kind ?? null,
    roundLabel: snapshot.round_label ?? null,
    terminal: snapshot.status === 'completed' || snapshot.status === 'failed',
  };
}

export function applyEvent(state: SessionState, event: RunEvent): SessionState {
  const sequence = event.sequence ?? 0;
  if (sequence > 0 && sequence <= state.sequence) return state;
  let next = {...state, sequence: Math.max(state.sequence, sequence)};
  next = applyFailureEvent(next, event);
  if (event.agent_kind === 'chat') return applyChatEvent(next, event);
  if (event.agent_kind) next.agentKind = event.agent_kind;
  if (event.round_label) next.roundLabel = event.round_label;
  const runMap = applyRunMapEvent(
    {outerLoop: next.outerLoop, rounds: next.rounds, phases: next.phases},
    event,
  );
  next.outerLoop = runMap.outerLoop;
  next.rounds = runMap.rounds;
  next.phases = runMap.phases;

  const data = event.data;
  if (data?.kind === 'tool_call' || data?.kind === 'tool_result') {
    next.typedToolEvents = true;
  }
  if (data?.kind === 'todo_update') {
    const items = (data.todos ?? []).map(todo => ({
      content: String(todo.content),
      status: String(todo.status),
    }));
    const agentKind = event.agent_kind ?? null;
    const roundNumber = roundNumberFromLabel(event.round_label);
    next.todoPhases = [
      ...next.todoPhases.filter(
        phase => phase.agentKind !== agentKind || phase.roundNumber !== roundNumber,
      ),
      {agentKind, roundNumber, items},
    ].slice(-100);
  }
  if (data?.kind === 'usage_update') {
    next.usage = {
      inputTokens: data.input_tokens,
      contextWindow: data.context_window ?? null,
      model: data.model ?? null,
    };
  }

  // Prefer typed tool events; fall back to legacy tool-channel chunks only
  // for streams that never produce typed events (old event files / replays).
  const suppressed =
    data?.kind === 'agent_output_chunk' && data.channel === 'tool' && next.typedToolEvents;
  if (!suppressed) {
    const entry = eventToConversationEntry(event);
    if (entry !== null) next.conversation = appendConversation(next.conversation, entry);
  }

  if (event.type === 'run_started') {
    next.status = 'running';
    if (event.data?.kind === 'run_started') next.maxRounds = event.data.max_rounds;
  }
  if (event.type === 'configuration_failed') {
    next.status = 'failed';
    next.terminal = true;
  }
  if (event.type === 'run_finished') {
    next.status = 'completed';
    next.terminal = true;
  }
  if (event.type === 'run_failed' || event.type === 'run_interrupted') {
    next.status = 'failed';
    next.terminal = true;
  }
  return next;
}

/**
 * The chat shows the exchange: what was asked, and what came back. The chat
 * agent's narration, tool turns, and phase markers are run plumbing; they
 * belong in the transcript, and here they only bury the answer.
 */
const CHAT_KINDS: ReadonlySet<ConversationEntry['kind']> = new Set(['user', 'assistant', 'result']);

function applyChatEvent(state: SessionState, event: RunEvent): SessionState {
  const next = {...state};
  const data = event.data;
  if (data?.kind === 'tool_call' || data?.kind === 'tool_result') {
    next.chatTypedToolEvents = true;
  }
  const suppressed =
    data?.kind === 'agent_output_chunk' && data.channel === 'tool' && next.chatTypedToolEvents;
  if (suppressed) return next;
  const entry = eventToConversationEntry(event);
  // The chat is a question and its answer. The chat agent's own diagnostics and
  // phase markers are run plumbing: they belong in the transcript, and in the
  // chat they only bury the answer the operator asked for.
  if (entry !== null && !CHAT_KINDS.has(entry.kind)) return next;
  if (entry !== null) {
    next.chatConversation = appendConversation(next.chatConversation, entry);
  }
  return next;
}

export function selectNextAgent(state: SessionState): SessionState {
  const phases = visiblePhases(state);
  if (phases.length === 0) return state;
  const current = state.selectedAgentKind;
  const index = current === null ? -1 : phases.findIndex(phase => phase.kind === current);
  const next = phases[(index + 1 + phases.length) % phases.length];
  return {...state, selectedAgentKind: next?.kind ?? null, roundFocus: 'agents', overlay: null};
}

export function selectPreviousAgent(state: SessionState): SessionState {
  const phases = visiblePhases(state);
  if (phases.length === 0) return state;
  const current = state.selectedAgentKind;
  const index = current === null ? 0 : phases.findIndex(phase => phase.kind === current);
  const previous = phases[(index - 1 + phases.length) % phases.length];
  return {
    ...state,
    selectedAgentKind: previous?.kind ?? null,
    roundFocus: 'agents',
    overlay: null,
  };
}

export function selectNextRound(state: SessionState): SessionState {
  const rounds = stripRounds(state);
  if (rounds.length === 0) return state;
  const visible = visibleRoundNumber(state);
  const index = visible === null ? -1 : rounds.findIndex(round => round.number === visible);
  const next = rounds[(index + 1 + rounds.length) % rounds.length];
  return {
    ...state,
    selectedRound: next?.number ?? null,
    selectedAgentKind: null,
    selectedEntryId: null,
    overlay: null,
  };
}

export function selectPreviousRound(state: SessionState): SessionState {
  const rounds = stripRounds(state);
  if (rounds.length === 0) return state;
  const visible = visibleRoundNumber(state);
  const index = visible === null ? 0 : rounds.findIndex(round => round.number === visible);
  const previous = rounds[(index - 1 + rounds.length) % rounds.length];
  return {
    ...state,
    selectedRound: previous?.number ?? null,
    selectedAgentKind: null,
    selectedEntryId: null,
    overlay: null,
  };
}

export function selectRound(state: SessionState, roundNumber: number): SessionState {
  if (!stripRounds(state).some(round => round.number === roundNumber)) return state;
  return {
    ...state,
    selectedRound: roundNumber,
    selectedAgentKind: null,
    selectedEntryId: null,
    overlay: null,
  };
}

export function clearAgentSelection(state: SessionState): SessionState {
  return {
    ...state,
    selectedAgentKind: null,
    selectedEntryId: null,
    roundFocus: 'transcript',
    overlay: null,
  };
}

/**
 * Picks one agent out of the round, or clears the pick when it is already the
 * selected one, so clicking a node twice behaves the way a toggle should. The
 * transcript follows the selection, so the entry cursor starts over with it.
 */
export function selectAgent(state: SessionState, kind: string): SessionState {
  const selected = state.selectedAgentKind === kind ? null : kind;
  return {
    ...state,
    selectedAgentKind: selected,
    selectedEntryId: null,
    roundFocus: 'agents',
    overlay: null,
  };
}

/**
 * Moves the transcript cursor. The cursor is an entry id rather than an index
 * because the visible list changes underneath it as the run streams: an id
 * still points at the same turn after new output arrives.
 */
export function selectNextEntry(state: SessionState, delta: number, id?: string): SessionState {
  const entries = visibleConversation(state);
  if (entries.length === 0) return state;
  // A click names the entry outright; the keys step from wherever the cursor is.
  if (id !== undefined) {
    if (!entries.some(entry => entry.id === id)) return state;
    return {...state, selectedEntryId: state.selectedEntryId === id ? null : id};
  }
  const current =
    state.selectedEntryId === null
      ? -1
      : entries.findIndex(entry => entry.id === state.selectedEntryId);
  // No cursor yet: step in from the end the operator is moving away from.
  const start = current === -1 ? (delta > 0 ? -1 : entries.length) : current;
  const index = Math.min(entries.length - 1, Math.max(0, start + delta));
  return {...state, selectedEntryId: entries[index]?.id ?? null};
}

/**
 * Moves the round view's keys one pane over, in the direction the arrow points.
 * The agent graph is the left pane and the transcript the right one, so this is
 * the spatial move; at either edge it stays put rather than wrapping, because
 * wrapping across the width of the screen is disorienting.
 */
export function focusRound(state: SessionState, focus: RoundFocus): SessionState {
  if (state.roundFocus === focus) return state;
  // Arriving at the graph with nothing picked out puts the cursor on the agent
  // whose turns are on screen, so Tab starts from where the operator is looking.
  if (focus === 'agents' && state.selectedAgentKind === null) {
    const phases = visiblePhases(state);
    const active = phases.find(phase => phase.status === 'active') ?? phases[0];
    return {...state, roundFocus: focus, selectedAgentKind: active?.kind ?? null};
  }
  return {...state, roundFocus: focus};
}

export function clearEntrySelection(state: SessionState): SessionState {
  if (state.selectedEntryId === null) return state;
  return {...state, selectedEntryId: null};
}

/** Moves the todo cursor within the visible phase's list. */
export function selectNextTodo(state: SessionState, delta: number): SessionState {
  const todos = visibleTodos(state);
  if (todos.length === 0) return state;
  const current = state.selectedTodoIndex;
  const start = current === null ? (delta > 0 ? -1 : todos.length) : current;
  const index = Math.min(todos.length - 1, Math.max(0, start + delta));
  return {...state, selectedTodoIndex: index};
}

export interface ErrorReport {
  scope: ErrorScope;
  severity?: ErrorSeverity;
  title?: string;
  diagnostic?: Diagnostic | null;
  agentKind?: string | null;
  roundLabel?: string | null;
  invocationId?: string | null;
}

/**
 * Records an error independently of any particular view. A terminal event
 * commonly repeats an invocation failure, so equivalent reports promote the
 * current banner instead of burying its cause beneath a duplicate.
 */
export function reportError(
  state: SessionState,
  message: string,
  report: ErrorReport,
): SessionState {
  const diagnostic = report.diagnostic ?? null;
  const scope = diagnostic?.scope ?? report.scope;
  const severity = diagnosticSeverity(diagnostic?.severity) ?? report.severity ?? 'recoverable';
  const banner: ErrorBannerState = {
    title: report.title ?? errorTitle(scope),
    message: diagnostic?.summary || message || 'An unknown error occurred.',
    detail: diagnostic?.detail ?? null,
    hint: diagnostic?.hint ?? null,
    diagnosticId: diagnostic?.id ?? null,
    severity,
    scope,
    agentKind: report.agentKind ?? null,
    roundLabel: report.roundLabel ?? null,
    invocationId: report.invocationId ?? null,
    count: 1,
  };
  const existing = state.errorBanner;
  if (existing === null || !equivalentError(existing, banner)) {
    return {...state, errorBanner: banner};
  }
  const promoted = existing.severity === 'fatal' || severity === 'fatal' ? 'fatal' : 'recoverable';
  return {
    ...state,
    errorBanner: {
      ...existing,
      message: moreInformativeMessage(existing.message, banner.message),
      detail: moreInformativeMessage(existing.detail ?? '', banner.detail ?? '') || null,
      hint: moreInformativeMessage(existing.hint ?? '', banner.hint ?? '') || null,
      severity: promoted,
      title: promoted === 'fatal' ? banner.title : existing.title,
      scope: promoted === 'fatal' ? banner.scope : existing.scope,
      diagnosticId: existing.diagnosticId ?? banner.diagnosticId,
      agentKind: existing.agentKind ?? banner.agentKind,
      roundLabel: existing.roundLabel ?? banner.roundLabel,
      invocationId: existing.invocationId ?? banner.invocationId,
      count: existing.count + 1,
    },
  };
}

function applyFailureEvent(state: SessionState, event: RunEvent): SessionState {
  const data = event.data;
  if (event.diagnostic !== null && event.diagnostic !== undefined) {
    return reportError(state, event.text ?? '', {
      scope: 'protocol',
      diagnostic: event.diagnostic,
      ...(event.type === 'run_interrupted' ? {title: 'Run interrupted'} : {}),
      agentKind: event.agent_kind ?? null,
      roundLabel: event.round_label ?? null,
      invocationId: event.invocation_id ?? null,
    });
  }
  if (data?.kind === 'configuration_failed') {
    return reportError(state, formatConfigurationFailure(event), {
      scope: 'configuration',
      severity: 'fatal',
      title: 'Configuration failed',
    });
  }
  if (
    data?.kind === 'invocation_finished' &&
    ((data.error !== null && data.error !== undefined) || event.status === 'failed')
  ) {
    return reportError(state, data.error || event.text || 'Agent invocation failed.', {
      scope: 'invocation',
      title: 'Invocation failed',
      agentKind: event.agent_kind ?? null,
      roundLabel: event.round_label ?? null,
      invocationId: event.invocation_id ?? null,
    });
  }
  if (event.type === 'run_failed' || event.type === 'run_interrupted') {
    const interruption =
      data?.kind === 'run_interrupted'
        ? `${data.reason}${data.signal === null ? '' : ` (${data.signal})`}`
        : '';
    const fallback = event.type === 'run_failed' ? 'Run failed.' : 'Run interrupted.';
    return reportError(state, event.text || interruption || fallback, {
      scope: 'run',
      severity: 'fatal',
      title: event.type === 'run_interrupted' ? 'Run interrupted' : 'Run failed',
      agentKind: event.agent_kind ?? null,
      roundLabel: event.round_label ?? null,
      invocationId: event.invocation_id ?? null,
    });
  }
  return state;
}

/** Keep a terminal wrapper's extra context when it repeats an invocation error. */
function moreInformativeMessage(current: string, later: string): string {
  if (later.length >= current.length) return later;
  const currentLines = current.split('\n').length;
  const laterLines = later.split('\n').length;
  return laterLines > currentLines ? later : current;
}

function equivalentError(left: ErrorBannerState, right: ErrorBannerState): boolean {
  if (left.diagnosticId !== null && left.diagnosticId === right.diagnosticId) return true;
  if (left.invocationId !== null && left.invocationId === right.invocationId) return true;
  const leftMessage = left.message.trim();
  const rightMessage = right.message.trim();
  if (leftMessage === rightMessage) return true;
  // Terminal wrappers often add only an exception prefix around an invocation
  // error. Fold those together, but do not equate short generic diagnostics.
  return (
    Math.min(leftMessage.length, rightMessage.length) >= 24 &&
    (leftMessage.includes(rightMessage) || rightMessage.includes(leftMessage))
  );
}

function errorTitle(scope: ErrorScope): string {
  const titles: Record<ErrorScope, string> = {
    configuration: 'Configuration failed',
    invocation: 'Invocation failed',
    phase: 'Phase failed',
    run: 'Run failed',
    protocol: 'Protocol error',
    request: 'Request failed',
    transport: 'Connection lost',
    input: 'Input error',
  };
  return titles[scope];
}

function diagnosticSeverity(
  severity: Diagnostic['severity'] | undefined,
): ErrorSeverity | undefined {
  if (severity === undefined) return undefined;
  return severity === 'fatal' ? 'fatal' : 'recoverable';
}

function formatConfigurationFailure(event: RunEvent): string {
  const data = event.data;
  if (data?.kind !== 'configuration_failed') return event.text || 'Configuration failed.';
  const sections = [data.message];
  if (data.usage) sections.push(data.usage);
  sections.push(`Code: ${data.code} · Stage: ${data.stage}`);
  return sections.join('\n\n');
}

function eventToConversationEntry(event: RunEvent): ConversationEntry | null {
  const data = event.data;
  const id = String(event.sequence ?? `${event.timestamp}-${event.type}`);
  const agentKind = event.agent_kind ? {agentKind: event.agent_kind} : {};
  const roundLabel = event.round_label ? {roundLabel: event.round_label} : {};
  const roundNumber = roundNumberFromLabel(event.round_label);
  const roundFields = {
    ...roundLabel,
    ...(roundNumber === null ? {} : {roundNumber}),
  };
  if (data?.kind === 'configuration_failed') {
    return {
      id,
      kind: 'result',
      content: formatConfigurationFailure(event),
      label: 'Configuration failed',
      tone: 'failure',
    };
  }
  if (data?.kind === 'chat') {
    return {
      id,
      kind: 'assistant',
      content: data.answer,
      label: 'Answer',
      ...agentKind,
      ...roundFields,
    };
  }
  if (data?.kind === 'agent_output_chunk') {
    const kind =
      data.channel === 'assistant'
        ? 'assistant'
        : data.channel === 'prompt'
          ? 'prompt'
          : data.channel === 'analysis'
            ? 'analysis'
            : data.channel === 'tool'
              ? 'tool'
              : 'diagnostic';
    const invocationId = event.invocation_id ?? undefined;
    return {
      id,
      kind,
      content: data.content,
      label: labelFor(event, data.channel),
      ...agentKind,
      ...roundFields,
      turnId: invocationId ?? id,
      ...(invocationId === undefined ? {} : {invocationId}),
      startsTurn:
        kind === 'tool' && data.channel === 'tool' && data.content.trimStart().startsWith('→ '),
      ...(kind === 'tool' && data.channel === 'tool' && data.content.trimStart().startsWith('→ ')
        ? {toolCall: data.content}
        : {}),
    };
  }
  if (data?.kind === 'tool_call') {
    const call = formatToolCall(data.tool, data.args ?? {});
    const invocationId = event.invocation_id ?? undefined;
    return {
      id,
      kind: 'tool',
      content: call,
      label: labelFor(event, 'tool'),
      ...agentKind,
      ...roundFields,
      turnId: invocationId ?? id,
      ...(invocationId === undefined ? {} : {invocationId}),
      startsTurn: true,
      toolCall: call,
      toolName: data.tool,
      ...(data.call_id === null || data.call_id === undefined ? {} : {toolCallId: data.call_id}),
    };
  }
  if (data?.kind === 'tool_result') {
    const invocationId = event.invocation_id ?? undefined;
    return {
      id,
      kind: 'tool',
      content: data.content,
      label: labelFor(event, 'tool'),
      ...(data.is_error ? {tone: 'failure' as const} : {}),
      ...agentKind,
      ...roundFields,
      turnId: invocationId ?? id,
      toolName: data.tool,
      ...(data.call_id === null || data.call_id === undefined ? {} : {toolCallId: data.call_id}),
      ...(invocationId === undefined ? {} : {invocationId}),
    };
  }
  if (data?.kind === 'subprocess_output') {
    return {
      id,
      kind: 'subprocess',
      content: data.content,
      label: `${data.process_kind} · ${data.stream}`,
      ...agentKind,
      ...roundFields,
    };
  }
  if (event.type === 'phase_started') {
    return {
      id,
      kind: 'status',
      content: 'started',
      label: labelFor(event, 'phase'),
      ...agentKind,
      ...roundFields,
    };
  }
  if (data?.kind === 'judge_result') {
    return {
      id,
      kind: 'result',
      content: data.feedback || `Judge returned ${data.verdict}.`,
      label: `Judge · ${data.verdict.toUpperCase()}`,
      tone: data.verdict === 'pass' ? 'success' : 'failure',
      ...agentKind,
      ...roundFields,
    };
  }
  if (data?.kind === 'benchmark_result') {
    return {
      id,
      kind: 'result',
      content: `${data.metric}: ${data.value} ${data.unit}`,
      label: 'Benchmark',
      tone: 'success',
      ...agentKind,
      ...roundFields,
    };
  }
  if (data?.kind === 'round_finished') {
    const tone =
      data.judge_verdict === 'pass'
        ? ('success' as const)
        : data.judge_verdict === 'fail'
          ? ('failure' as const)
          : ('normal' as const);
    return {
      id,
      kind: 'result',
      content: `${data.attempts} attempt(s)`,
      label: `${event.round_label ?? 'Round'} · ${data.judge_verdict.toUpperCase()}`,
      tone,
      ...agentKind,
      ...roundFields,
    };
  }
  if (event.type === 'run_failed' || event.type === 'run_interrupted') {
    return {
      id,
      kind: 'result',
      content: event.text || 'Run interrupted.',
      label: 'Run failed',
      tone: 'failure',
      ...agentKind,
      ...roundFields,
    };
  }
  return null;
}

function labelFor(event: RunEvent, fallback: string): string {
  const phase = event.agent_kind ?? fallback;
  return event.round_label ? `${phase} · ${event.round_label}` : phase;
}

function appendConversation(
  previous: ConversationEntry[],
  incoming: ConversationEntry,
): ConversationEntry[] {
  if (incoming.kind === 'tool' && !incoming.startsTurn && incoming.toolName !== undefined) {
    const target = findToolCall(previous, incoming);
    if (target !== -1) {
      return previous.map((entry, index) =>
        index === target ? mergeToolResult(entry, incoming) : entry,
      );
    }
  }
  const last = previous.at(-1);
  if (
    last &&
    incoming.kind === 'tool' &&
    last.kind === 'tool' &&
    last.invocationId === incoming.invocationId &&
    !incoming.startsTurn
  ) {
    return [...previous.slice(0, -1), mergeToolResult(last, incoming)];
  }
  if (
    last &&
    last.kind === incoming.kind &&
    last.turnId === incoming.turnId &&
    (incoming.kind === 'assistant' || incoming.kind === 'prompt' || incoming.kind === 'analysis')
  ) {
    return [...previous.slice(0, -1), {...last, content: last.content + incoming.content}];
  }
  return capConversation([...previous, incoming]);
}

/**
 * A run can outlive any fixed number of entries, but a round the operator can
 * still open must keep all of its turns: a half-evicted round reads as a round
 * that never ran. So the cap is generous, and when it is reached the oldest
 * round goes as a whole rather than the oldest few lines.
 */
const MAX_CONVERSATION_ENTRIES = 20_000;

function capConversation(entries: ConversationEntry[]): ConversationEntry[] {
  if (entries.length <= MAX_CONVERSATION_ENTRIES) return entries;
  const oldestRound = entries.find(entry => entry.roundNumber !== undefined)?.roundNumber;
  if (oldestRound === undefined) return entries.slice(-MAX_CONVERSATION_ENTRIES);
  const kept = entries.filter(
    entry => entry.roundNumber === undefined || entry.roundNumber > oldestRound,
  );
  // Nothing to drop by round (one huge round): fall back to trimming the front.
  return kept.length === entries.length ? entries.slice(-MAX_CONVERSATION_ENTRIES) : kept;
}

function findToolCall(previous: ConversationEntry[], result: ConversationEntry): number {
  const indices = Array.from(previous.keys());
  if (result.toolCallId !== undefined) indices.reverse();
  for (const index of indices) {
    const candidate = previous[index];
    if (
      candidate?.kind !== 'tool' ||
      candidate.toolCall === undefined ||
      candidate.toolResponse !== undefined ||
      candidate.invocationId !== result.invocationId
    ) {
      continue;
    }
    if (result.toolCallId !== undefined) {
      if (candidate.toolCallId === result.toolCallId) return index;
      continue;
    }
    if (candidate.toolName === result.toolName) return index;
  }
  return -1;
}

function mergeToolResult(call: ConversationEntry, result: ConversationEntry): ConversationEntry {
  const separator = call.content.endsWith('\n') || result.content.startsWith('\n') ? '' : '\n';
  return {
    ...call,
    content: call.content + separator + result.content,
    toolResponse: (call.toolResponse ?? '') + (call.toolResponse ? separator : '') + result.content,
    ...(result.tone === undefined ? {} : {tone: result.tone}),
  };
}

/**
 * Returns to the experiment log. Per-round output is reachable only by opening
 * a hypothesis, so there is no unfiltered live view to fall back to and the
 * log is never dismissed.
 */
export function showLive(state: SessionState): SessionState {
  return {
    ...state,
    overlay: null,
    chatOpen: false,
    hypothesisScope: null,
    selectedRound: null,
    selectedAgentKind: null,
    experimentLog: state.experimentLog ?? {
      entries: [],
      selectedId: null,
      pending: true,
      error: null,
    },
  };
}

export function showDetail(
  state: SessionState,
  content: string,
  kind: OverlayPanel['kind'] = 'detail',
): SessionState {
  return {...state, overlay: {kind, content}};
}

export function statusText(state: SessionState): string {
  const base = `${state.status} · ${state.agentKind ?? 'starting'} · ${state.roundLabel ?? 'no round yet'}`;
  if (state.usage === null) return base;
  const used = formatTokenCount(state.usage.inputTokens);
  const meter =
    state.usage.contextWindow === null
      ? used
      : `${used}/${formatTokenCount(state.usage.contextWindow)}`;
  return `${base} · ${meter} tokens`;
}

function formatTokenCount(count: number): string {
  if (count < 1_000) return String(count);
  if (count < 1_000_000) return `${Math.floor(count / 1_000)}k`;
  return `${(count / 1_000_000).toFixed(1)}M`;
}

export function visibleConversation(state: SessionState): ConversationEntry[] {
  const roundNumber = visibleRoundNumber(state);
  return state.conversation.filter(entry => {
    if (roundNumber !== null && entry.roundNumber !== roundNumber) return false;
    if (state.selectedAgentKind !== null && entry.agentKind !== state.selectedAgentKind) {
      return false;
    }
    return true;
  });
}

export function visiblePhases(state: SessionState): AgentPhase[] {
  return visibleRunMapPhases(state.phases, visibleRoundNumber(state));
}

export function toggleTodos(state: SessionState): SessionState {
  return {...state, todosExpanded: !state.todosExpanded};
}

/**
 * The todo list for the phase the operator is looking at, following the same
 * scoping rules as the conversation filter. Entries whose events carried no
 * agent or round stamp (legacy streams) match any scope rather than vanish.
 */
export function visibleTodos(state: SessionState): TodoItem[] {
  const roundNumber = visibleRoundNumber(state);
  const matchesRound = (phase: PhaseTodos): boolean =>
    roundNumber === null || phase.roundNumber === roundNumber || phase.roundNumber === null;
  const latestFirst = [...state.todoPhases].reverse();
  if (state.selectedAgentKind !== null) {
    const selected = state.selectedAgentKind;
    return (
      latestFirst.find(
        phase => (phase.agentKind === selected || phase.agentKind === null) && matchesRound(phase),
      )?.items ?? []
    );
  }
  if (state.selectedRound !== null) {
    // Browsing a round without an agent selected: show the round's most
    // recently updated list, i.e. its final todo state.
    return latestFirst.find(matchesRound)?.items ?? [];
  }
  // Live view: follow the currently active agent so a phase that never
  // emits todos shows nothing instead of the previous phase's leftovers.
  return (
    latestFirst.find(
      phase =>
        (phase.agentKind === state.agentKind || phase.agentKind === null) && matchesRound(phase),
    )?.items ?? []
  );
}

export function visibleRoundNumber(state: SessionState): number | null {
  const scope = state.hypothesisScope;
  if (scope !== null && state.selectedRound === null) {
    // Opening a hypothesis lands on one of its rounds rather than on nothing.
    // Leaving the round unset used to mean "the whole trajectory", which drew an
    // empty agent strip and an empty transcript for any run whose events are not
    // all round-stamped: a blank view where a round was asked for.
    const rounds = state.rounds.filter(round => scope.rounds.includes(round.number));
    const active = [...rounds].reverse().find(round => round.status === 'active');
    return active?.number ?? rounds.at(-1)?.number ?? scope.rounds.at(-1) ?? null;
  }
  return visibleRunMapRoundNumber(stripRounds(state), state.selectedRound);
}

/** The rounds owned by the hypothesis on screen, or every round outside one. */
export function scopedRounds(state: SessionState): RoundSummary[] {
  const scope = state.hypothesisScope;
  if (scope === null) return state.rounds;
  return state.rounds.filter(round => scope.rounds.includes(round.number));
}

/**
 * What the strip shows and what round navigation moves through: every round of
 * the run, including ones it has not reached yet. A run announces how many
 * rounds it intends to take, so the strip can show the shape of the whole run
 * from the first round rather than growing one chip at a time. Rounds beyond
 * what has happened carry ``planned`` and open an empty view, which is the
 * honest thing to show for a round that has not run.
 */
export function stripRounds(state: SessionState): RoundSummary[] {
  const highest = Math.max(state.maxRounds ?? 0, ...state.rounds.map(round => round.number), 0);
  if (highest === 0) return state.rounds;
  const known = new Map(state.rounds.map(round => [round.number, round]));
  const rounds: RoundSummary[] = [];
  for (let number = 1; number <= highest; number += 1) {
    rounds.push(known.get(number) ?? {number, status: 'planned'});
  }
  return rounds;
}

const MAX_TOOL_ARG_LEN = 80;

function formatToolCall(tool: string, args: Record<string, unknown>): string {
  const parts = Object.entries(args).map(([key, value]) => {
    const isString = typeof value === 'string';
    let rendered = isString ? value : (JSON.stringify(value) ?? String(value));
    if (rendered.length > MAX_TOOL_ARG_LEN) {
      rendered = `${rendered.slice(0, MAX_TOOL_ARG_LEN)}...`;
    }
    return isString ? `${key}="${rendered}"` : `${key}=${rendered}`;
  });
  return `→ ${tool}(${parts.join(', ')})\n`;
}
