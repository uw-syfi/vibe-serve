import {BoxRenderable, type CliRenderer, TextRenderable} from '@opentui/core';
import {hasActiveAgentTiming} from '../round-timing.js';
import {type RoundSummary, roundAgentElapsedMs} from '../run-map.js';
import type {SessionController} from '../session-controller.js';
import type {SessionState} from '../session-model.js';
import {scopedRounds, visibleRoundNumber} from '../session-model.js';
import {elapsedLabel} from './previews.js';
import type {Theme} from './theme.js';

export class RoundStripView {
  readonly output: BoxRenderable;
  #theme: Theme;
  #renderedState: SessionState | null = null;
  #elapsedTimer: ReturnType<typeof setInterval> | null = null;
  #runningRound: {
    round: RoundSummary;
    selected: number | null;
    text: TextRenderable;
  } | null = null;

  constructor(
    private readonly renderer: CliRenderer,
    private readonly controller: SessionController,
    theme: Theme,
  ) {
    this.#theme = theme;
    this.output = new BoxRenderable(renderer, {
      id: 'round-strip',
      width: '100%',
      height: 3,
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.borderStrong,
      paddingLeft: 1,
      paddingRight: 1,
      title: ' Rounds ',
    });
  }

  applyTheme(theme: Theme): void {
    this.#theme = theme;
    this.output.borderColor = theme.borderStrong;
    this.#renderedState = null;
  }

  render(state: SessionState): void {
    if (state === this.#renderedState) return;
    this.#renderedState = state;
    this.#clear();
    // Inside a hypothesis the strip covers that hypothesis's rounds only, so
    // round navigation stays within the trajectory the operator opened.
    const rounds = scopedRounds(state);
    if (rounds.length === 0) {
      this.output.add(
        new TextRenderable(this.renderer, {
          content: 'Waiting for rounds...',
          fg: this.#theme.textSubtle,
          width: '100%',
        }),
      );
      return;
    }
    const row = new BoxRenderable(this.renderer, {
      id: 'round-strip-row',
      width: '100%',
      flexDirection: 'row',
    });
    const selected = visibleRoundNumber(state);
    const runningRound = latestActiveRoundNumber(rounds);
    for (const round of rounds.slice(-8)) {
      row.add(this.#renderRound(round, {selected, runningRound}));
    }
    this.output.add(row);
    this.#syncElapsedTimer();
  }

  destroy(): void {
    this.#stopElapsedTimer();
  }

  #clear(): void {
    this.#runningRound = null;
    this.#stopElapsedTimer();
    for (const child of [...this.output.getChildren()]) {
      this.output.remove(child);
      child.destroyRecursively();
    }
  }

  #renderRound(
    round: RoundSummary,
    viewState: {selected: number | null; runningRound: number | null},
  ): TextRenderable {
    const {selected, runningRound} = viewState;
    const isSelected = round.number === selected;
    const isRunning = round.number === runningRound;
    const text = new TextRenderable(this.renderer, {
      content: this.#roundLabel(round, selected),
      fg: isRunning ? this.#theme.success : this.#theme.textPrimary,
      ...(isSelected ? {bg: this.#theme.selectedSurface} : {}),
      onMouseUp: () => this.controller.selectRound(round.number),
    });
    if (isRunning && hasActiveAgentTiming(round)) this.#runningRound = {round, selected, text};
    return text;
  }

  #roundLabel(round: RoundSummary, selected: number | null): string {
    const isSelected = round.number === selected;
    const elapsed = round.status === 'active' ? ` ${elapsedLabel(roundAgentElapsedMs(round))}` : '';
    return `${isSelected ? '[' : ' '} r${round.number}${elapsed} ${isSelected ? ']' : ' '}`;
  }

  #syncElapsedTimer(): void {
    if (this.#runningRound === null || this.#elapsedTimer !== null) return;
    this.#elapsedTimer = setInterval(() => {
      if (this.#runningRound === null) return;
      const {round, selected, text} = this.#runningRound;
      text.content = this.#roundLabel(round, selected);
    }, 1000);
  }

  #stopElapsedTimer(): void {
    if (this.#elapsedTimer === null) return;
    clearInterval(this.#elapsedTimer);
    this.#elapsedTimer = null;
  }
}

function latestActiveRoundNumber(rounds: RoundSummary[]): number | null {
  return [...rounds].reverse().find(round => round.status === 'active')?.number ?? null;
}
