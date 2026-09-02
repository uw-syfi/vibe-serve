import {BoxRenderable, type CliRenderer, TextRenderable} from '@opentui/core';
import type {SessionState} from '../session-model.js';
import type {Theme} from './theme.js';

type OverlayKind = NonNullable<SessionState['overlay']>['kind'];

const TITLE: Record<OverlayKind, string> = {
  detail: 'Command',
  help: 'Help',
  error: 'Error',
};

function borderFor(theme: Theme, kind: OverlayKind): string {
  if (kind === 'help') return theme.success;
  if (kind === 'error') return theme.error;
  return theme.info;
}

export class OverlayView {
  readonly output: BoxRenderable;
  #theme: Theme;
  #renderedKind: OverlayKind | null = null;
  #renderedContent = '';

  constructor(
    private readonly renderer: CliRenderer,
    theme: Theme,
  ) {
    this.#theme = theme;
    this.output = new BoxRenderable(renderer, {
      id: 'overlay',
      width: '70%',
      height: '60%',
      position: 'absolute',
      left: '15%',
      top: '18%',
      flexDirection: 'column',
      paddingLeft: 1,
      paddingRight: 1,
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.info,
      backgroundColor: theme.elevatedSurface,
      zIndex: 25,
    });
  }

  applyTheme(theme: Theme): void {
    this.#theme = theme;
    this.output.backgroundColor = theme.elevatedSurface;
    this.output.borderColor = borderFor(theme, this.#renderedKind ?? 'detail');
    this.#renderedKind = null;
    this.#renderedContent = '';
  }

  render(state: SessionState): void {
    const overlay = state.overlay;
    if (overlay === null) {
      this.output.visible = false;
      return;
    }
    this.output.visible = true;
    if (this.#renderedKind === overlay.kind && this.#renderedContent === overlay.content) return;
    this.#renderedKind = overlay.kind;
    this.#renderedContent = overlay.content;
    this.output.borderColor = borderFor(this.#theme, overlay.kind);
    this.output.title = ` ${TITLE[overlay.kind]} `;
    this.#clear();
    this.output.add(
      new TextRenderable(this.renderer, {
        content: overlay.content,
        fg:
          overlay.kind === 'error'
            ? this.#theme.conversation.failure.content
            : this.#theme.textPrimary,
        width: '100%',
      }),
    );
    this.output.add(
      new TextRenderable(this.renderer, {
        content: 'Esc to close',
        fg: this.#theme.textSubtle,
        width: '100%',
      }),
    );
  }

  #clear(): void {
    for (const child of [...this.output.getChildren()]) {
      this.output.remove(child);
      child.destroyRecursively();
    }
  }
}
