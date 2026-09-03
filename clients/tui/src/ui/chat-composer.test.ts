import {afterEach, describe, expect, it, setSystemTime} from 'bun:test';
import {BoxRenderable, type Renderable} from '@opentui/core';
import {createTestRenderer, type TestRendererSetup} from '@opentui/core/testing';
import type {SessionController} from '../session-controller.js';
import {initialSessionState, type SessionState} from '../session-model.js';
import {createChatDraft, pendingComposerLabel} from './chat-composer.js';
import {ChatPaneView} from './chat-pane.js';
import {paneTitle} from './focus.js';
import {createMarkdownStyle} from './styles.js';
import {resolveTheme} from './theme.js';

describe('pending composer title', () => {
  it('shows a spinner frame and the elapsed wait', () => {
    expect(pendingComposerLabel(0, 0)).toBe('Message · ⠋ 0s');
    expect(pendingComposerLabel(1, 5_000)).toBe('Message · ⠙ 5s');
  });

  it('wraps the frame index so a long wait keeps animating', () => {
    expect(pendingComposerLabel(10, 1_000)).toBe(pendingComposerLabel(0, 1_000));
    expect(pendingComposerLabel(23, 1_000)).toBe(pendingComposerLabel(3, 1_000));
  });
});

/**
 * The spinner's state lives in the composer, but only the surfaces around it
 * know whether they are on screen, so these drive a real `ChatPaneView` and
 * read the title its box ended up with. A question that completes while the
 * dock is hidden is the case `activate` alone cannot see: it does not run
 * while hidden, so nothing stops the timer or moves the elapsed epoch.
 */
describe('composer spinner across a hidden surface', () => {
  const cleanup: Array<() => void> = [];
  const EPOCH = Date.UTC(2026, 8, 3, 12, 0, 0);

  afterEach(() => {
    for (const destroy of cleanup.splice(0).reverse()) destroy();
    setSystemTime();
  });

  /** The composer only calls back on input, which none of these tests send. */
  const controller = {
    focusPane: () => {},
    submitChat: () => {},
  } as unknown as SessionController;

  function stateWith(pending: boolean): SessionState {
    return {...initialSessionState(), chatPending: pending};
  }

  function composerTitle(node: Renderable): string | null {
    for (const child of node.getChildren()) {
      if (child instanceof BoxRenderable && child.id.endsWith('-composer-box')) {
        return child.title ?? null;
      }
      const found = composerTitle(child);
      if (found !== null) return found;
    }
    return null;
  }

  interface Dock {
    render: (pending: boolean, visible: boolean) => void;
    title: () => string | null;
    destroy: () => void;
  }

  async function dockedChat(): Promise<Dock> {
    const testRenderer: TestRendererSetup = await createTestRenderer({width: 120, height: 24});
    const theme = resolveTheme(null);
    const markdownStyle = createMarkdownStyle(theme);
    const view = new ChatPaneView(
      testRenderer.renderer,
      controller,
      markdownStyle,
      theme,
      createChatDraft(),
    );
    testRenderer.renderer.root.add(view.output);
    cleanup.push(() => {
      view.destroy();
      view.output.destroyRecursively();
      markdownStyle.destroy();
      testRenderer.renderer.destroy();
    });
    return {
      render: (pending, visible) => view.render(stateWith(pending), visible, 40),
      title: () => composerTitle(view.output),
      destroy: () => view.destroy(),
    };
  }

  it('clears the spinner when the answer lands while the dock is hidden', async () => {
    const dock = await dockedChat();
    setSystemTime(new Date(EPOCH));
    dock.render(true, true);
    expect(dock.title()).toBe(paneTitle(pendingComposerLabel(0, 0), false));

    dock.render(true, false);
    dock.render(false, false);

    expect(dock.title()).toBe(paneTitle('Message', false));
  });

  it('restarts the elapsed wait for the question asked after a hidden one', async () => {
    const dock = await dockedChat();
    setSystemTime(new Date(EPOCH));
    dock.render(true, true);
    setSystemTime(new Date(EPOCH + 30_000));
    dock.render(true, false);
    dock.render(false, false);

    setSystemTime(new Date(EPOCH + 40_000));
    dock.render(true, true);

    // The new question's wait, not the one that ran out of sight.
    expect(dock.title()).toBe(paneTitle(pendingComposerLabel(0, 0), false));
  });

  it('animates again after a destroy rather than staying frozen', async () => {
    const dock = await dockedChat();
    setSystemTime(new Date(EPOCH));
    dock.render(true, true);
    dock.destroy();

    setSystemTime(new Date(EPOCH + 7_000));
    dock.render(true, true);

    expect(dock.title()).toBe(paneTitle(pendingComposerLabel(0, 0), false));
  });
});
