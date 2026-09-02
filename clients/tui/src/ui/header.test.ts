import {describe, expect, it} from 'bun:test';
import {initialSessionState, type SessionState} from '../session-model.js';
import {MIN_WIDTH, renderHeader, runStateText, usageText} from './header.js';
import {displayWidth} from './text-width.js';

const WIDE = 200;
const NARROW = 100;

function stateWith(
  overrides: Partial<SessionState> = {},
  core: Partial<SessionState['core']> = {},
) {
  const base = initialSessionState();
  return {...base, ...overrides, core: {...base.core, ...core}};
}

/** The live example from #517, as the backend actually reports it. */
function runningImplementer(): SessionState {
  return stateWith(
    {
      hypothesisScope: {
        id: 'H-01',
        label: 'Preallocated lock-free SPSC ring · r1',
        title: 'Preallocated lock-free SPSC ring',
        rounds: [1],
      },
    },
    {
      status: 'running',
      agentKind: 'implementer',
      roundLabel: 'round-1-retry-2-implementer',
      usage: {inputTokens: 223_000, contextWindow: 400_000, model: 'claude-opus-5'},
    },
  );
}

/** The same run, with a claim of a chosen length. */
function withTitle(title: string): SessionState {
  const base = runningImplementer();
  return {
    ...base,
    hypothesisScope: {id: 'H-01', label: `${title} · r1`, title, rounds: [1]},
  };
}

/** How many times `needle` appears in `text`, for grapheme-integrity checks. */
function occurrences(text: string, needle: string): number {
  return text.split(needle).length - 1;
}

describe('header', () => {
  it('replaces the raw label line from the issue', () => {
    const header = renderHeader(runningImplementer(), false, WIDE);
    expect(header).toBe(
      'VibeSys · running · implementing · attempt 2 · Preallocated lock-free SPSC ring · 223k/400k context',
    );
  });

  it('never renders a backend round label, in any loop mode', () => {
    const labels = [
      'round-1-plan',
      'round-1-pre',
      'round-1-retry-1-implementer',
      'round-2-retry-3-judge',
      'gen-2-cand-1-mutator',
      'impl issue #7 att2',
      'perf_eval iter 4',
    ];
    for (const roundLabel of labels) {
      const header = renderHeader(stateWith({}, {roundLabel, status: 'running'}), false, WIDE);
      expect(header).not.toContain(roundLabel);
      expect(header).not.toMatch(/round-\d|gen-\d|cand-\d|retry-\d|att\d/);
    }
  });

  it('fits 100 columns whole, where the old header clipped the title', () => {
    const header = renderHeader(runningImplementer(), false, NARROW);
    expect(header.length).toBeLessThanOrEqual(NARROW);
    expect(header).toContain('Preallocated lock-free SPSC ring');
    expect(header).not.toEndWith('…');
  });

  it('gives up the token meter before the hypothesis title', () => {
    // 80 columns cannot hold both; the claim is the one worth keeping.
    const header = renderHeader(runningImplementer(), false, 80);
    expect(header.length).toBeLessThanOrEqual(80);
    expect(header).toContain('Preallocated lock-free SPSC ring');
    expect(header).not.toContain('context');
    expect(header).not.toEndWith('…');
  });

  it('drops the dialog hint before the selected agent, and both before the title', () => {
    const state = {
      ...runningImplementer(),
      selectedAgentKind: 'implementer',
      overlay: {kind: 'detail' as const, content: 'x'},
    };
    expect(renderHeader(state, false, WIDE)).toContain('Esc: close dialog');
    const squeezed = renderHeader(state, false, NARROW);
    expect(squeezed).not.toContain('Esc: close dialog');
    expect(squeezed).not.toContain('selected implementer');
    expect(squeezed).toContain('Preallocated lock-free SPSC ring');
  });

  it('shortens a long title only once nothing cheaper is left to drop', () => {
    // The real title from the recorded run: too long for 60 columns even with
    // every weaker segment already gone, so it shortens rather than vanishing.
    const state = withTitle(
      'Preallocated lock-free SPSC ring replaces the Mutex<VecDeque> hot path',
    );
    const header = renderHeader(state, false, 60);
    expect(header.length).toBeLessThanOrEqual(60);
    expect(header).not.toContain('context');
    expect(header).toContain('Preallocated');
    expect(header).toEndWith('…');
  });

  it('stops shortening the title at the point it stops identifying anything', () => {
    const state = withTitle(
      'Preallocated lock-free SPSC ring replaces the Mutex<VecDeque> hot path',
    );
    // 34 columns leaves the title exactly its floor of twelve cells.
    expect(renderHeader(state, false, 34)).toBe('VibeSys · running · Preallocated…');
    // 22 leaves it less than that, so it goes entirely rather than becoming
    // `Prealloc…`, and the run stays legible without it.
    expect(renderHeader(state, false, 22)).toBe('VibeSys · running');
  });

  it('keeps the brand and the run state whole down to the minimum width', () => {
    for (const width of [200, 100, 60, 34, MIN_WIDTH]) {
      const header = renderHeader(runningImplementer(), false, width);
      expect(header.startsWith('VibeSys · running')).toBe(true);
      expect(displayWidth(header)).toBeLessThanOrEqual(width);
    }
  });

  it('holds the minimum width for every run state it can name', () => {
    // MIN_WIDTH is sized for the widest of these, so none of them is cut.
    const states = ['running', 'paused', 'completed', 'failed', 'connecting'];
    for (const status of states) {
      const header = renderHeader(stateWith({}, {status}), false, MIN_WIDTH);
      expect(header).toBe(`VibeSys · ${status}`);
    }
    const dropped = stateWith({eventStreamAvailable: false}, {status: 'running'});
    expect(renderHeader(dropped, false, MIN_WIDTH)).toBe('VibeSys · disconnected');
  });

  it('cuts the line below the minimum width instead of dropping the state', () => {
    // Narrower than MIN_WIDTH the brand and the state do not fit together, so
    // the guarantee stops there and the line is cut and marked. Dropping the
    // state instead would leave a header that says only `VibeSys`.
    const header = renderHeader(runningImplementer(), false, MIN_WIDTH - 6);
    expect(displayWidth(header)).toBeLessThanOrEqual(MIN_WIDTH - 6);
    expect(header).toBe('VibeSys · runni…');
    expect(renderHeader(runningImplementer(), false, 0)).toBe('');
  });
});

describe('width in terminal cells', () => {
  it('budgets a CJK title in cells rather than code units', () => {
    // 30 ideographs are 30 code units and 60 terminal cells. Budgeted by
    // `String.length` the whole title reads as fitting 60 columns with room to
    // spare, and lays out over 80.
    const title = '\u74b0'.repeat(30);
    const header = renderHeader(withTitle(title), false, 60);
    expect(displayWidth(header)).toBeLessThanOrEqual(60);
    expect(header.length).toBeLessThan(60);
    expect(header).toEndWith('\u2026');
  });

  it('never cuts an emoji in half', () => {
    const state = withTitle(`${'\u{1f680}'.repeat(20)} to orbit`);
    for (const width of [30, 44, 45, 60]) {
      const header = renderHeader(state, false, width);
      expect(displayWidth(header)).toBeLessThanOrEqual(width);
      // With the `u` flag this matches only an unpaired surrogate, which is
      // what a code-unit slice through a rocket leaves behind.
      expect(header).not.toMatch(/[\ud800-\udfff]/u);
    }
  });

  it('keeps a joined emoji sequence whole', () => {
    // One grapheme, eight code units. A cut inside it leaves separate people
    // or a dangling joiner.
    const family = '\u{1f468}\u200d\u{1f469}\u200d\u{1f467}';
    const state = withTitle(`${family.repeat(12)} crew`);
    for (const width of [30, 40, 55]) {
      const header = renderHeader(state, false, width);
      expect(displayWidth(header)).toBeLessThanOrEqual(width);
      expect(header).not.toMatch(/[\ud800-\udfff]/u);
      expect(header).not.toContain('\u200d\u2026');
      // Every family carries one of each figure, so unequal counts mean a cut
      // landed inside a cluster.
      expect(occurrences(header, '\u{1f468}')).toBe(occurrences(header, '\u{1f467}'));
    }
  });

  it('drops a wide grapheme that would straddle the budget', () => {
    // 44 columns leaves the title 23 cells, which is 11 rockets and a half.
    // The half goes, so the line comes out a cell under rather than a cell
    // over.
    const header = renderHeader(withTitle('\u{1f680}'.repeat(20)), false, 44);
    expect(displayWidth(header)).toBe(43);
  });
});

describe('token meter', () => {
  it('shows a ratio only when the count is inside the window', () => {
    const state = stateWith(
      {},
      {usage: {inputTokens: 118_000, contextWindow: 400_000, model: null}},
    );
    expect(usageText(state)).toBe('118k/400k context');
  });

  it('drops the denominator rather than reporting over 100 percent', () => {
    // The issue's observation: 472k/400k rendered as 118 percent and read as
    // an overflow error.
    const state = stateWith(
      {},
      {usage: {inputTokens: 472_000, contextWindow: 400_000, model: null}},
    );
    expect(usageText(state)).toBe('472k tokens');
    expect(usageText(state)).not.toContain('/');
  });

  it('shows a bare count when no window is known', () => {
    const state = stateWith({}, {usage: {inputTokens: 5_400, contextWindow: null, model: null}});
    expect(usageText(state)).toBe('5k tokens');
  });

  it('says nothing before any usage is reported', () => {
    expect(usageText(initialSessionState())).toBeNull();
    const zero = stateWith({}, {usage: {inputTokens: 0, contextWindow: 400_000, model: null}});
    expect(usageText(zero)).toBeNull();
  });
});

describe('run state', () => {
  it('reports the backend status by default', () => {
    expect(runStateText(stateWith({}, {status: 'running'}))).toBe('running');
    expect(runStateText(stateWith({}, {status: 'completed'}))).toBe('completed');
  });

  it('shows a pause the operator asked for, over the backend status', () => {
    const paused = stateWith({pauseOverride: 'paused'}, {status: 'running'});
    expect(runStateText(paused)).toBe('paused');
    expect(renderHeader(paused, false, WIDE)).toContain('paused');
  });

  it('clears a paused boot snapshot once the operator resumes', () => {
    // The snapshot is read once at boot and the backend never pushes a status
    // change, so `core.status` stays `paused` for the rest of the session. A
    // boolean override cannot say "resumed" here: false is what it starts as.
    const booted = stateWith({}, {status: 'paused'});
    expect(runStateText(booted)).toBe('paused');
    const resumed = stateWith({pauseOverride: 'resumed'}, {status: 'paused'});
    expect(runStateText(resumed)).toBe('running');
    expect(renderHeader(resumed, false, WIDE)).not.toContain('paused');
  });

  it('leaves a status that is not paused alone when the operator resumes', () => {
    const resumed = stateWith({pauseOverride: 'resumed'}, {status: 'running'});
    expect(runStateText(resumed)).toBe('running');
  });

  it('lets a terminal status outrank a pause that was never resumed', () => {
    // `run_finished`, `run_failed` and `run_interrupted` all set `terminal`.
    // The run is over, so the operator's standing pause is no longer true.
    for (const status of ['completed', 'failed', 'interrupted']) {
      const ended = stateWith({pauseOverride: 'paused'}, {status, terminal: true});
      expect(runStateText(ended)).toBe(status);
      expect(renderHeader(ended, false, WIDE)).not.toContain('paused');
    }
  });

  it('shows a lost event stream while the run is still going', () => {
    const dropped = stateWith({eventStreamAvailable: false}, {status: 'running'});
    expect(runStateText(dropped)).toBe('disconnected');
    // A finished run whose socket closed is not a disconnected run.
    const finished = stateWith(
      {eventStreamAvailable: false},
      {status: 'completed', terminal: true},
    );
    expect(runStateText(finished)).toBe('completed');
  });
});
