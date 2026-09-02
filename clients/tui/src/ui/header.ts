/**
 * The header line: what the run is doing, in operator terms.
 *
 * It replaces a line that interpolated backend identifiers verbatim
 * (`VibeSys · running · implementer · round-1-retry-1-implementer · 118k/400k
 * tokens · Preallocated lock-free SPSC ring · r1`). Three things were wrong
 * with that and are fixed here:
 *
 * - Raw `round_label` strings meant nothing to an operator and duplicated the
 *   rounds strip and Agents pane. `describePhase` turns them into words.
 * - The token meter divided one number by another that does not bound it, so
 *   it could read 118 percent and look like an overflow error. `usageText`
 *   only prints a denominator when the numerator is actually inside it.
 * - Everything was concatenated with no width budget, so at 100 columns the
 *   hypothesis title, the one segment carrying what the run is trying, was
 *   clipped while `round-1-retry-1-implementer` survived. `renderHeader` drops
 *   whole segments in a defined order instead, and budgets in terminal cells
 *   rather than UTF-16 code units, which are not the same count.
 */
import {runStatusLabel, type SessionState} from '../session-model.js';
import {describePhase, phaseText} from './phase-label.js';
import {displayWidth, truncateToWidth} from './text-width.js';

/** Separator between header segments, matching the rest of the interface. */
const SEPARATOR = ' · ';

const BRAND = 'VibeSys';

/**
 * One header segment and how readily it is given up when the line does not
 * fit. Lower priority goes first.
 *
 * The order encodes the judgement in #517: the hypothesis title outranks the
 * token meter, the selected-agent note, and the dialog hint, because it is the
 * only segment that says what the run is trying to achieve.
 */
interface Segment {
  text: string;
  priority: number;
  /**
   * Never dropped. The brand and the run state are the header's floor, which
   * is what `MIN_WIDTH` is the width of.
   */
  required?: boolean;
  /**
   * Shortened to fit rather than dropped. Only the hypothesis title is: half a
   * claim still says which claim, where a dropped one says nothing. Every other
   * segment is a fixed phrase that means nothing in part.
   */
  truncatable?: boolean;
}

/**
 * The title outranks the phase deliberately. The phase is also on the Agents
 * pane and implied by the transcript; the title is the only place the run says
 * what it is trying, and #517 requires it to survive a narrow terminal ahead of
 * less useful segments.
 */
const PRIORITY = {
  brand: 100,
  state: 90,
  title: 70,
  phase: 60,
  usage: 40,
  selection: 30,
  hint: 10,
} as const;

/** Cells below which a shortened title is noise rather than an identifier. */
const MIN_TITLE = 12;

/**
 * The one run state that is not a backend status: the event stream is gone, so
 * whatever the status last said can no longer be trusted to be current.
 */
const DISCONNECTED = 'disconnected';

/** The widest word `runStateText` produces; every status label is shorter. */
const WIDEST_STATE = DISCONNECTED;

/**
 * The narrowest terminal this header supports, in cells.
 *
 * At this width or wider the brand and the run state both render whole: they
 * are the two segments `renderHeader` will not drop, and no run state is wider
 * than `WIDEST_STATE`. Below it neither is guaranteed, because there is no
 * room for both: the line is cut and marked with an ellipsis, which is what a
 * terminal that narrow can be told. That bound is the property the header
 * holds; "the brand and the run state always survive" is not true at every
 * width and was never true of the dropping loop.
 */
export const MIN_WIDTH = displayWidth(`${BRAND}${SEPARATOR}${WIDEST_STATE}`);

/**
 * Run state in words.
 *
 * The backend owns the run's lifecycle and publishes every move through it, so
 * the run state is the run status and `runStatusLabel` is how it reads. There
 * is no client-local pause flag to prefix it or to outrank it, which is what
 * keeps the header from claiming a state the backend disagrees with.
 *
 * The header adds one word of its own. Without a trustworthy event stream the
 * status is only as current as the last event that arrived, so a live run is
 * reported as `disconnected` rather than as a value that has stopped moving. A
 * run that has ended is exempt: a status it never leaves cannot go stale, and a
 * finished run whose socket closed is finished rather than disconnected.
 */
export function runStateText(state: SessionState): string {
  if (!state.core.terminal && !state.eventStreamAvailable) return DISCONNECTED;
  return runStatusLabel(state.core.status);
}

/**
 * The token meter, with an honest denominator or none at all.
 *
 * `inputTokens` is the context the last agent call carried, and `contextWindow`
 * is a static per-model lookup that can be absent or stale. When the two
 * disagree, the count alone is the true statement; a ratio over 100 percent is
 * not.
 */
export function usageText(state: SessionState): string | null {
  const usage = state.core.usage;
  if (usage === null || usage.inputTokens <= 0) return null;
  const used = formatTokenCount(usage.inputTokens);
  if (usage.contextWindow === null || usage.inputTokens > usage.contextWindow) {
    return `${used} tokens`;
  }
  // Labeled `context` rather than `tokens`: the denominator bounds one call's
  // context, and calling it "tokens" invited reading it as run spend.
  return `${used}/${formatTokenCount(usage.contextWindow)} context`;
}

function formatTokenCount(count: number): string {
  if (count < 1_000) return String(count);
  if (count < 1_000_000) return `${Math.floor(count / 1_000)}k`;
  return `${(count / 1_000_000).toFixed(1)}M`;
}

/** Segments for the current state, before any width budgeting. */
export function headerSegments(state: SessionState, showLog: boolean): Segment[] {
  const segments: Segment[] = [
    {text: BRAND, priority: PRIORITY.brand, required: true},
    {text: runStateText(state), priority: PRIORITY.state, required: true},
  ];

  if (showLog) {
    segments.push({text: 'experiments', priority: PRIORITY.phase});
  } else {
    const phase = phaseText(describePhase(state.core.roundLabel, state.core.agentKind));
    if (phase !== null) segments.push({text: phase, priority: PRIORITY.phase});
    if (state.hypothesisScope !== null) {
      segments.push({
        text: state.hypothesisScope.title,
        priority: PRIORITY.title,
        truncatable: true,
      });
    }
  }

  const usage = usageText(state);
  if (usage !== null) segments.push({text: usage, priority: PRIORITY.usage});

  if (!showLog && state.selectedAgentKind !== null) {
    segments.push({text: `selected ${state.selectedAgentKind}`, priority: PRIORITY.selection});
  }

  const dialogOpen = state.chatOpen || state.overlay !== null || state.themePicker !== null;
  if (dialogOpen) segments.push({text: 'Esc: close dialog', priority: PRIORITY.hint});

  return segments;
}

/**
 * Assembles the header to fit `width` terminal cells.
 *
 * Whole segments are dropped, lowest priority first, rather than clipping the
 * line: a truncated fixed phrase reads as a rendering bug, while a missing
 * token meter reads as a header that had better things to say. Only once
 * everything droppable is gone does the title shorten, and only then, which is
 * what keeps it from being clipped ahead of less useful segments.
 *
 * The budget is in cells, not code units, so a header the caller sized for 40
 * columns of CJK does not lay out over 50 of them, and the fallback cut lands
 * on a grapheme boundary rather than inside an emoji.
 *
 * At `MIN_WIDTH` and wider the brand and the run state both survive whole.
 * Narrower than that they do not fit together, and the line is cut and marked.
 */
export function renderHeader(state: SessionState, showLog: boolean, width: number): string {
  const kept = headerSegments(state, showLog);
  while (displayWidth(joined(kept)) > width) {
    const weakest = weakestIndex(kept);
    // Only the brand and the run state are left, and they do not fit: below
    // MIN_WIDTH the line is cut rather than emptied.
    if (weakest === null) break;
    // Nothing left that is cheaper than the title: shortening it now is the
    // only move that does not cost a whole segment, and by this point it is no
    // longer being clipped ahead of anything less useful.
    if (kept[weakest]?.truncatable === true && shrinkTitle(kept, width)) break;
    kept.splice(weakest, 1);
  }
  const line = joined(kept);
  if (displayWidth(line) <= width) return line;
  if (width <= 0) return '';
  // One cell for the ellipsis that says the rest was cut.
  return `${truncateToWidth(line, width - 1)}…`;
}

/** The segment given up first, or null once only required ones are left. */
function weakestIndex(segments: Segment[]): number | null {
  let weakest: number | null = null;
  for (const [index, candidate] of segments.entries()) {
    if (candidate.required === true) continue;
    const current = weakest === null ? undefined : segments[weakest];
    // `<=` makes the later of two equal-priority segments the weaker one, so
    // trailing notes go before leading ones.
    if (current === undefined || candidate.priority <= current.priority) weakest = index;
  }
  return weakest;
}

/**
 * Shortens the truncatable segment to close the overrun, if what remains is
 * still long enough to identify. Returns whether the line now fits.
 */
function shrinkTitle(segments: Segment[], width: number): boolean {
  const index = segments.findIndex(segment => segment.truncatable === true);
  const segment = segments[index];
  if (segment === undefined) return false;
  // What the rest of the line, its separators included, already spends.
  const rest = displayWidth(joined(segments)) - displayWidth(segment.text);
  // One cell of the budget goes to the ellipsis.
  const budget = width - rest - 1;
  if (budget < MIN_TITLE) return false;
  const shortened = truncateToWidth(segment.text, budget).trimEnd();
  // A wide grapheme dropped at the cut, or trailing space, can leave less than
  // the budget allowed, and below MIN_TITLE the caller is better off dropping.
  if (displayWidth(shortened) < MIN_TITLE) return false;
  segments[index] = {...segment, text: `${shortened}…`};
  return displayWidth(joined(segments)) <= width;
}

function joined(segments: Segment[]): string {
  return segments.map(segment => segment.text).join(SEPARATOR);
}
