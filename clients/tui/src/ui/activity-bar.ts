import {type CliRenderer, TextRenderable} from '@opentui/core';
import type {ActiveAgentExecution} from '@vibesys/core-state';
import type {SessionState} from '../session-model.js';
import {visibleActiveExecutions} from '../session-model.js';
import {agentRuntimeLabel} from './agent-runtime-label.js';
import type {Theme} from './theme.js';

/** The one braille spinner every in-flight indicator animates. */
export const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
export const SPINNER_INTERVAL_MS = 120;
/** Status for executions visible in the selected agent conversation. */
export class ActivityBarView {
  readonly output: TextRenderable;
  #executions: ActiveAgentExecution[] = [];
  #frame = 0;
  #timer: ReturnType<typeof setInterval> | null = null;

  constructor(renderer: CliRenderer, theme: Theme, id = 'activity-bar') {
    this.output = new TextRenderable(renderer, {
      id,
      height: 1,
      width: '100%',
      wrapMode: 'none',
      truncate: true,
      fg: theme.textMuted,
      content: '',
      visible: false,
    });
  }

  render(state: SessionState, visible = true): void {
    this.#executions = visible ? visibleActiveExecutions(state) : [];
    this.output.visible = this.#executions.length > 0;
    this.#refresh();
    this.#syncTimer();
  }

  applyTheme(theme: Theme): void {
    this.output.fg = theme.textMuted;
  }

  destroy(): void {
    if (this.#timer !== null) clearInterval(this.#timer);
    this.#timer = null;
  }

  #syncTimer(): void {
    if (this.#executions.length === 0) {
      if (this.#timer !== null) clearInterval(this.#timer);
      this.#timer = null;
      return;
    }
    if (this.#timer !== null) return;
    this.#timer = setInterval(() => {
      this.#frame = (this.#frame + 1) % SPINNER_FRAMES.length;
      this.#refresh();
    }, SPINNER_INTERVAL_MS);
  }

  #refresh(): void {
    const spinner = SPINNER_FRAMES[this.#frame] ?? SPINNER_FRAMES[0];
    const nowMs = Date.now();
    if (this.#executions.length === 1) {
      const execution = this.#executions[0];
      if (execution === undefined) return;
      const runtime = runtimeSuffix(execution);
      this.output.content = `${spinner} ${roleLabel(execution.agentKind)} · ${activitySummary(execution)}${runtime} · ${elapsed(execution.startedAt, nowMs)}`;
      return;
    }
    const summaries = this.#executions
      .slice(0, 3)
      .map(
        execution =>
          `${roleLabel(execution.agentKind)}: ${activitySummary(execution)}${runtimeSuffix(execution)}`,
      )
      .join(' · ');
    const remainder = this.#executions.length > 3 ? ` · +${this.#executions.length - 3} more` : '';
    this.output.content = `${spinner} ${this.#executions.length} agents active · ${summaries}${remainder}`;
  }
}

export function activitySummary(_execution: ActiveAgentExecution): string {
  return 'Working';
}

export function runtimeSuffix(execution: ActiveAgentExecution): string {
  const label = agentRuntimeLabel(execution.provider, execution.model);
  return label === null ? '' : ` · ${label}`;
}

function roleLabel(role: string): string {
  if (role === '') return 'Agent';
  return role.charAt(0).toUpperCase() + role.slice(1).replaceAll('_', ' ');
}

function elapsed(startedAt: string, nowMs: number): string {
  const milliseconds = nowMs - Date.parse(startedAt);
  const seconds = Number.isFinite(milliseconds) ? Math.max(0, Math.floor(milliseconds / 1000)) : 0;
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}
