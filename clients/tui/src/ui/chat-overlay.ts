import {
  BoxRenderable,
  type CliRenderer,
  ScrollBoxRenderable,
  type SyntaxStyle,
} from '@opentui/core';
import type {SessionController} from '../session-controller.js';
import {chatThreadHeading, type SessionState} from '../session-model.js';
import {ChatComposerView, type ChatDraft} from './chat-composer.js';
import {ConversationView} from './conversation.js';
import type {Theme} from './theme.js';

/**
 * The centred modal's four edges as fractions of the terminal: 80% of the
 * columns, centred, and 76% of the rows starting a tenth of the way down.
 */
const MODAL_LEFT = 0.1;
const MODAL_RIGHT = 0.9;
const MODAL_TOP = 0.1;
const MODAL_BOTTOM = 0.86;

/** Screen rectangle the chat occupies when it shares the row with a pane. */
export interface PaneBounds {
  left: number;
  width: number;
  top: number;
  height: number;
}

function samePaneBounds(left: PaneBounds | null, right: PaneBounds | null): boolean {
  if (left === null || right === null) return left === right;
  return (
    left.left === right.left &&
    left.width === right.width &&
    left.top === right.top &&
    left.height === right.height
  );
}

export class ChatOverlayView {
  readonly output: BoxRenderable;
  readonly #transcript: ScrollBoxRenderable;
  readonly #conversation: ConversationView;
  readonly #composer: ChatComposerView;
  #bounds: PaneBounds | null = null;

  constructor(
    private readonly renderer: CliRenderer,
    controller: SessionController,
    markdownStyle: SyntaxStyle,
    theme: Theme,
    draft: ChatDraft,
  ) {
    this.output = new BoxRenderable(renderer, {
      id: 'chat-overlay',
      position: 'absolute',
      flexDirection: 'column',
      paddingLeft: 1,
      paddingRight: 1,
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.conversation.analysis.label,
      backgroundColor: theme.elevatedSurface,
      title: ' Experiment chat ',
      zIndex: 20,
      visible: false,
    });
    this.#transcript = new ScrollBoxRenderable(renderer, {
      id: 'chat-transcript',
      width: '100%',
      flexGrow: 1,
      stickyScroll: true,
      stickyStart: 'bottom',
      viewportCulling: true,
      verticalScrollbarOptions: {showArrows: true},
    });
    this.#conversation = new ConversationView(renderer, controller, markdownStyle, theme, {
      selectConversation: state => state.chatConversation,
      emptyContent: 'Ask a question about the current experiment, its progress, or a failure.',
      // Answers are agent-authored markdown; the operator's own messages stay
      // verbatim so typed ** or # is never concealed as markup. The chat keeps
      // answering after the run turns terminal, so it never switches to the
      // finalized parse that would leave a fresh answer blank until a redraw.
      markdownKinds: ['assistant'],
      markdownStreaming: true,
    });
    this.#composer = new ChatComposerView(
      renderer,
      draft,
      value => void controller.submitChat(value),
      theme,
      'chat-modal',
    );
    this.#applyModalGeometry();
    this.#transcript.add(this.#conversation.output);
    this.output.add(this.#transcript);
    // Anchored to the composer, matching the docked pane: the same commands
    // read the same way whichever presentation the chat is in.
    this.output.add(this.#composer.menu);
    this.output.add(this.#composer.output);
  }

  /**
   * Confines the chat to the left pane while a visualization is on screen, so
   * the operator can ask about what they are looking at without covering it.
   * ``null`` restores the centred modal geometry.
   */
  setPaneBounds(bounds: PaneBounds | null): void {
    // The modal measures itself against the terminal, so it is recomputed even
    // when the bounds did not change: a resize changes the answer.
    if (bounds === null) {
      this.#bounds = null;
      this.#applyModalGeometry();
      return;
    }
    if (samePaneBounds(this.#bounds, bounds)) return;
    this.#bounds = bounds;
    this.output.left = bounds.left;
    this.output.width = Math.max(1, bounds.width);
    this.output.top = bounds.top;
    this.output.height = Math.max(3, bounds.height);
  }

  /**
   * Places the centred modal on whole cells. Left as percentages, its edges
   * land mid-row at most terminal heights, and the layout rounds a child's
   * offset from its parent separately from that child's size: at a fractional
   * offset the two disagree and the transcript either runs a row into the
   * composer or leaves a row of the modal's floor blank. Rounding each edge
   * here is what the layout already did to the modal itself, so the rectangle
   * is unchanged, but every offset inside it is now whole.
   */
  #applyModalGeometry(): void {
    const {terminalWidth, terminalHeight} = this.renderer;
    const left = Math.round(terminalWidth * MODAL_LEFT);
    const top = Math.round(terminalHeight * MODAL_TOP);
    this.output.left = left;
    this.output.width = Math.max(1, Math.round(terminalWidth * MODAL_RIGHT) - left);
    this.output.top = top;
    this.output.height = Math.max(3, Math.round(terminalHeight * MODAL_BOTTOM) - top);
  }

  applyTheme(theme: Theme, markdownStyle: SyntaxStyle): void {
    this.output.borderColor = theme.conversation.analysis.label;
    this.output.backgroundColor = theme.elevatedSurface;
    this.#composer.applyTheme(theme);
    this.#conversation.applyTheme(theme, markdownStyle);
  }

  render(state: SessionState): void {
    this.output.visible = state.chatOpen;
    // Ahead of the visibility gate: an answer that lands while the modal is
    // closed still has to stop the composer's spinner.
    this.#composer.syncPending(state.chatPending, state.chatOpen);
    if (!state.chatOpen) return;
    this.output.title = ` ${chatThreadHeading(state)} `;
    this.#composer.activate(Math.max(1, this.output.width - 4), true, state.chatPending);
    this.#composer.renderMenu(state);
    this.#conversation.render(state);
    this.#transcript.scrollTo(this.#transcript.scrollHeight);
  }

  focus(): void {
    this.#composer.focus();
  }

  isComposerEmpty(): boolean {
    return this.#composer.isEmpty();
  }

  navigateSuggestions(direction: 1 | -1): boolean {
    return this.#composer.navigateSuggestions(direction);
  }

  completeSuggestion(): boolean {
    return this.#composer.completeSuggestion();
  }

  /** Releases the composer's spinner timer when the app tears down. */
  destroy(): void {
    this.#composer.destroy();
  }
}
