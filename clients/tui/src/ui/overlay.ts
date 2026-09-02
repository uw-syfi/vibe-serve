import {BoxRenderable, type CliRenderer, ScrollBoxRenderable, TextRenderable} from '@opentui/core';
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
  readonly #scroll: ScrollBoxRenderable;

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
      // Above the chat modal (20), below the theme picker (30): a command ack
      // submitted from the modal chat has to be visible over it.
      zIndex: 25,
    });
    this.#scroll = new ScrollBoxRenderable(renderer, {
      id: 'overlay-scroll',
      width: '100%',
      flexGrow: 1,
    });
    this.output.add(this.#scroll);
  }

  scrollBy(delta: number): void {
    this.#scroll.scrollBy(delta, 'viewport');
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
    this.#scroll.scrollTo(0);
    this.#scroll.add(
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
        content: 'Esc to close · PgUp/PgDn: scroll',
        fg: this.#theme.textSubtle,
        width: '100%',
      }),
    );
  }

  #clear(): void {
    for (const child of [...this.#scroll.getChildren()]) {
      this.#scroll.remove(child);
      child.destroyRecursively();
    }
  }
}
