import {BoxRenderable, type CliRenderer, ScrollBoxRenderable, TextRenderable} from '@opentui/core';
import type {SessionController} from '../session-controller.js';
import {
  experimentLogVisible,
  focusedPane,
  type SessionState,
  statusText,
  stripRounds,
  visibleRoundNumber,
} from '../session-model.js';
import {ActivityBarView} from './activity-bar.js';
import {AgentMapView} from './agent-map.js';
import {createChatDraft} from './chat-composer.js';
import {ChatOverlayView} from './chat-overlay.js';
import {ChatPaneView, chatDockFits, chatPaneWidth} from './chat-pane.js';
import {RendererSelectionClipboard, type SelectionClipboard} from './clipboard.js';
import {createCommandInputPanel} from './command-input.js';
import {ConversationView} from './conversation.js';
import {ErrorBannerView} from './error-banner.js';
import {ExperimentLogView} from './experiment-log.js';
import {bindKeybindings} from './keybindings.js';
import {OverlayView} from './overlay.js';
import {RightPaneView, rightPaneWidth, splitFits} from './right-pane.js';
import {RoundStripView} from './round-strip.js';
import {createMarkdownStyle} from './styles.js';
import {resolveTheme, type ThemeName} from './theme.js';
import {ThemePickerView} from './theme-picker.js';
import {TodoStripView, todoStripWidth} from './todo-strip.js';

export interface OpenTuiApp {
  destroy(): void;
}

/** Which of the client's editors currently holds the cursor. */
type FocusTarget = 'command' | 'chat' | 'modal';

const KEY_HELP =
  '[/]: round · ←→: pane · ↑↓/Tab: within it · F4: zoom · /todos · /prompt · Ctrl+L: live';
const SCOPED_KEY_HELP =
  '[/]: round · ←→: pane · ↑↓/Tab: within it · F4: zoom · /todos · /prompt · Esc: back';
const LOG_KEY_HELP =
  '↑↓ or scroll: select · Enter/click: open hypothesis · F4: zoom · /open-round --N';
const LOG_CHAT_KEY_HELP =
  '↑↓: select · Enter/click: hypothesis · Ctrl+W: chat · F4: zoom · /open-round --N';
const HYPOTHESIS_KEY_HELP =
  '↑↓: select round · Enter/click: trajectory · PgUp/PgDn: scroll · Esc: hypotheses';
const SPLIT_KEY_HELP =
  'Ctrl+W: switch pane · F4: zoom focused pane · PgUp/PgDn: scroll · Esc: close pane';

/**
 * A round the run has not reached has no turns and never will until it runs.
 * Saying so beats "waiting for run events", which reads as something broken.
 */
function emptyTranscriptMessage(state: SessionState): string {
  const roundNumber = visibleRoundNumber(state);
  if (roundNumber === null) return 'Waiting for run events…';
  const round = stripRounds(state).find(item => item.number === roundNumber);
  if (round?.status === 'planned') return `Round ${roundNumber} has not run yet.`;
  return 'Waiting for run events…';
}

export function createOpenTuiApp(
  renderer: CliRenderer,
  controller: SessionController,
  clipboard: SelectionClipboard = new RendererSelectionClipboard(renderer),
): OpenTuiApp {
  let themeName: ThemeName = controller.state.themeName;
  let theme = resolveTheme(themeName);
  const root = new BoxRenderable(renderer, {
    id: 'app',
    width: '100%',
    height: '100%',
    flexDirection: 'column',
    backgroundColor: theme.canvas,
  });
  const header = new TextRenderable(renderer, {
    id: 'header',
    height: 1,
    fg: theme.accent,
    content: 'VibeSys · connecting',
  });
  const focusTranscript = (): void => {
    controller.focusPane('left');
    controller.focusRound('transcript');
  };
  const transcriptFrame = new BoxRenderable(renderer, {
    id: 'viewport',
    width: 'auto',
    flexGrow: 1,
    flexDirection: 'column',
    paddingLeft: 1,
    paddingRight: 1,
    border: true,
    borderStyle: 'rounded',
    borderColor: theme.border,
    onMouseUp: focusTranscript,
  });
  const viewport = new ScrollBoxRenderable(renderer, {
    id: 'transcript-scroll',
    width: '100%',
    flexGrow: 1,
    stickyScroll: true,
    stickyStart: 'bottom',
    viewportCulling: true,
    verticalScrollbarOptions: {showArrows: true},
    onMouseUp: focusTranscript,
  });
  const main = new BoxRenderable(renderer, {
    id: 'main',
    width: '100%',
    flexGrow: 1,
    flexDirection: 'row',
  });
  // The chat is a sibling of the entire workspace column, not just its
  // transcript row. Its composer therefore remains inside the chat border and
  // the pane spans the same height as the table plus command surface.
  const body = new BoxRenderable(renderer, {
    id: 'body',
    width: '100%',
    flexGrow: 1,
    flexDirection: 'row',
  });
  const workspace = new BoxRenderable(renderer, {
    id: 'workspace',
    flexGrow: 1,
    flexDirection: 'column',
  });
  const help = new TextRenderable(renderer, {
    id: 'key-help',
    height: 1,
    fg: theme.textSubtle,
    content: KEY_HELP,
  });
  let renderedKeyHelp = KEY_HELP;
  let transientStatus: string | null = null;
  let markdownStyle = createMarkdownStyle(theme);
  const roundStrip = new RoundStripView(renderer, controller, theme);
  const todoStrip = new TodoStripView(renderer, controller, theme);
  const errorBanner = new ErrorBannerView(renderer, theme, () => controller.dismissErrorBanner());
  const agentMap = new AgentMapView(renderer, controller, theme);
  const conversationActivityBar = new ActivityBarView(renderer, theme, 'conversation-activity-bar');
  const overlay = new OverlayView(renderer, theme);
  const experimentLog = new ExperimentLogView(renderer, controller, theme);
  const rightPane = new RightPaneView(renderer, theme, () => controller.focusPane('right'));
  const themePicker = new ThemePickerView(renderer, theme);
  // Scrolling back past the rendered window materializes the next block of
  // history. The viewport owns scroll position, so it absorbs the height the
  // revealed cards add and the reader keeps looking at the same content.
  const revealOlderEntries = (): void => {
    const heightBefore = viewport.scrollHeight;
    const top = viewport.scrollTop;
    if (!conversation.revealOlderEntries()) {
      // The window already starts at the oldest entry the client holds, so the
      // next block has to come from the backend. No scroll compensation here:
      // the backfill lands as a state update, and the view follows its window
      // anchor across the prepended entries, so the rendered cards, and with
      // them the scroll height, are unchanged. The reader stays put, and the
      // next gesture reveals the new entries through the branch below.
      void controller.loadOlderHistory();
      return;
    }
    const grew = viewport.scrollHeight - heightBefore;
    if (grew > 0) viewport.scrollTo(top + grew);
  };
  const conversation: ConversationView = new ConversationView(
    renderer,
    controller,
    markdownStyle,
    theme,
    {
      showsSelection: true,
      onFocusRequest: focusTranscript,
      onRevealOlder: revealOlderEntries,
    },
  );
  const chatDraft = createChatDraft();
  const chat = new ChatOverlayView(renderer, controller, markdownStyle, theme, chatDraft);
  const chatPane = new ChatPaneView(renderer, controller, markdownStyle, theme, chatDraft);
  // Composer drafts are per-thread. The shared ChatDraft stays the single
  // authority both chat surfaces read; switching threads swaps its content
  // and parks the outgoing thread's half-typed question for its return.
  const parkedDrafts = new Map<string, string>();
  let draftThreadId = controller.state.activeChatThreadId;
  // Clicking either box moves the pane focus to it, so the border, the hint,
  // and the cursor never disagree about which surface is taking keystrokes.
  const commandInput = createCommandInputPanel(
    renderer,
    value => void controller.submitCommand(value),
    theme,
    () => controller.focusPane('left'),
  );
  const bottom = new BoxRenderable(renderer, {
    id: 'bottom',
    width: '100%',
    flexShrink: 0,
    flexDirection: 'column',
    alignItems: 'stretch',
  });
  const commandColumn = new BoxRenderable(renderer, {
    id: 'command-column',
    flexGrow: 1,
    flexShrink: 0,
    flexDirection: 'column',
  });

  // A slash command and a key toggle the same prompt: the controller routes the
  // request, the transcript decides which prompt it applies to.
  controller.onTogglePrompt(() => conversation.toggleLatestPrompt());
  viewport.add(conversation.output);
  transcriptFrame.add(viewport);
  // The frame owns the shared horizontal inset for both turn cards and the
  // fixed activity row. Activity stays outside scrolling content, so a new
  // turn can change scroll height without moving the line.
  transcriptFrame.add(conversationActivityBar.output);
  main.add(agentMap.output);
  main.add(transcriptFrame);
  // The log lives in the main pane rather than floating over it: it is the
  // landing view, not a dialog.
  main.add(experimentLog.output);
  main.add(rightPane.output);
  commandColumn.add(help);
  // Absolute inside the command column rather than the root, so the list rises
  // out of the input it belongs to instead of across the chat beside it.
  commandColumn.add(commandInput.suggestions);
  commandColumn.add(commandInput.box);
  bottom.add(commandColumn);
  root.add(header);
  root.add(errorBanner.output);
  root.add(roundStrip.output);
  body.add(chatPane.output);
  workspace.add(main);
  workspace.add(todoStrip.output);
  workspace.add(bottom);
  body.add(workspace);
  root.add(body);
  root.add(overlay.output);
  root.add(themePicker.output);
  root.add(chat.output);
  renderer.root.add(root);
  commandInput.focus();

  const applyTheme = (next: ThemeName): (() => void) => {
    themeName = next;
    theme = resolveTheme(next);
    const previousMarkdownStyle = markdownStyle;
    markdownStyle = createMarkdownStyle(theme);
    root.backgroundColor = theme.canvas;
    header.fg = theme.accent;
    transcriptFrame.borderColor = theme.border;
    help.fg = theme.textSubtle;
    roundStrip.applyTheme(theme);
    todoStrip.applyTheme(theme);
    errorBanner.applyTheme(theme);
    agentMap.applyTheme(theme);
    conversationActivityBar.applyTheme(theme);
    overlay.applyTheme(theme);
    experimentLog.applyTheme(theme);
    rightPane.applyTheme(theme);
    themePicker.applyTheme(theme);
    conversation.applyTheme(theme, markdownStyle);
    chat.applyTheme(theme, markdownStyle);
    chatPane.applyTheme(theme, markdownStyle);
    commandInput.applyTheme(theme);
    return () => previousMarkdownStyle.destroy();
  };

  let focusTarget: FocusTarget = 'command';
  let lastState: SessionState = controller.state;
  const render = (state: SessionState): void => {
    lastState = state;
    const previewName = state.themePicker?.selected ?? state.themeName;
    const releasePreviousStyle = previewName === themeName ? undefined : applyTheme(previewName);
    if (state.activeChatThreadId !== draftThreadId) {
      // Park the outgoing thread's draft and restore the incoming thread's,
      // so switching never sends one thread's question to another's agent.
      parkedDrafts.set(draftThreadId, chatDraft.value);
      chatDraft.value = parkedDrafts.get(state.activeChatThreadId) ?? '';
      draftThreadId = state.activeChatThreadId;
    }
    const showLog = experimentLogVisible(state);
    const paneFocus = focusedPane(state);
    const zoomedPane = state.layout.zoomedPane;
    // A split only happens when the terminal can carry both panes. Narrower
    // than that, a visualization keeps the modal it had before the split
    // existed rather than squeezing two unreadable columns onto the screen.
    const splitOpen = state.layout.right !== null;
    const showSplit = zoomedPane === null && splitOpen && splitFits(renderer.terminalWidth);
    const showRightPane = zoomedPane === 'performance' || (zoomedPane === null && showSplit);
    const paneFallback = zoomedPane === null && splitOpen && !showSplit ? state.layout.right : null;
    // Whatever holds the left side, log or transcript, shares the row with the
    // pane rather than being replaced by it.
    const rightWidth =
      zoomedPane === 'performance'
        ? renderer.terminalWidth
        : showSplit
          ? rightPaneWidth(renderer.terminalWidth)
          : 0;
    const leftWidth = renderer.terminalWidth - rightWidth;
    // Measured here because this is the only place that knows the width, and
    // reported to the controller so a question goes where the operator can see
    // it. The layout below uses the measurement directly rather than waiting
    // for the state to come back, so a resize never draws a stale row.
    const dockFits = chatDockFits(renderer.terminalWidth, rightWidth);
    if (state.chatDockFits !== dockFits) controller.setChatDockFits(dockFits);
    const chatAvailable = showLog && state.hypothesisDetail === null && dockFits && !state.chatOpen;
    const showChatPane = chatAvailable && (zoomedPane === null || zoomedPane === 'chat');
    const chatWidth = showChatPane
      ? zoomedPane === 'chat'
        ? renderer.terminalWidth
        : chatPaneWidth(renderer.terminalWidth, rightWidth)
      : 0;
    const showExperimentLog = showLog && (zoomedPane === null || zoomedPane === 'experiments');
    const dialogOpen = state.chatOpen || state.overlay !== null || state.themePicker !== null;
    const returnHint = dialogOpen ? ' · Esc: close dialog' : '';
    const selection = state.selectedAgentKind ? ` · selected ${state.selectedAgentKind}` : '';
    const scope = state.hypothesisScope === null ? '' : ` · ${state.hypothesisScope.label}`;
    header.content = showLog
      ? `VibeSys · ${statusText(state)} · experiments`
      : `VibeSys · ${statusText(state)}${scope}${selection}${returnHint}`;
    errorBanner.render(state);
    // The log carries its own key hints in its footer, so when it shares the
    // row with a pane the global line is the place for the pane's keys.
    renderedKeyHelp = showSplit
      ? SPLIT_KEY_HELP
      : showLog
        ? state.hypothesisDetail !== null
          ? HYPOTHESIS_KEY_HELP
          : showChatPane
            ? LOG_CHAT_KEY_HELP
            : LOG_KEY_HELP
        : state.hypothesisScope === null
          ? KEY_HELP
          : SCOPED_KEY_HELP;
    help.content = transientStatus ?? renderedKeyHelp;
    // The round strip and agent map are per-round detail. They belong to a
    // hypothesis trajectory, not to the list of claims.
    const showAgents = !showLog && (zoomedPane === null ? !showSplit : zoomedPane === 'agents');
    const showTranscript = !showLog && (zoomedPane === null || zoomedPane === 'transcript');
    agentMap.output.visible = showAgents;
    transcriptFrame.visible = showTranscript;
    roundStrip.output.visible = !showLog && zoomedPane === null;
    todoStrip.output.visible = !showLog && zoomedPane === null;
    if (!showLog) {
      roundStrip.render(state);
      agentMap.render(state, zoomedPane === 'agents' ? renderer.terminalWidth : undefined);
      // The todo box sits under the agent pane and stops where it stops: the
      // todos belong to an agent, so running them under the transcript would
      // attach them to the wrong thing.
      conversation.setEmptyContent(emptyTranscriptMessage(state));
      const agentWidth = agentMap.output.width;
      todoStrip.render(
        state,
        typeof agentWidth === 'number' ? todoStripWidth(agentWidth, renderer.terminalWidth) : null,
      );
      conversation.render(state);
    }
    // The agent map is the first thing to give up room: it is a summary the
    // visualization largely supersedes while the split is open.
    agentMap.output.visible = showAgents;
    // Inside a round the transcript is one of two navigable panes, so it carries
    // the focus border whenever the round view's keys are on it.
    const transcriptFocused = !showLog && paneFocus === 'transcript';
    transcriptFrame.borderColor = transcriptFocused ? theme.borderFocus : theme.border;
    transcriptFrame.title = transcriptFocused ? ' ▸ Transcript ' : ' Transcript ';
    // Match the chat to the left pane's rectangle so it sits beside the
    // visualization instead of over it. Bounds come from the siblings that
    // actually occupy those rows, so a taller todo strip still fits.
    if (showSplit) {
      const errorHeight = state.errorBanner === null ? 0 : errorBanner.output.height;
      const top = header.height + errorHeight + (showLog ? 0 : roundStrip.output.height);
      const below = todoStrip.output.height + help.height + commandInput.box.height;
      chat.setPaneBounds({
        left: 1,
        width: leftWidth - 2,
        top,
        height: renderer.terminalHeight - top - below,
      });
    } else {
      chat.setPaneBounds(null);
    }
    chatPane.render(state, showChatPane, chatWidth);
    const chatInputFocused = showChatPane && state.layout.focus === 'chat';
    commandColumn.visible = zoomedPane !== 'chat';
    commandInput.setFocused(paneFocus === 'experiments' || paneFocus === 'transcript');
    // The command list completes the box it belongs to, and on this view that
    // box cannot open a chat that is already beside it.
    commandInput.setCommandContext({chatDocked: showChatPane});
    experimentLog.setAvailableWidth(showSplit || showChatPane ? leftWidth - chatWidth : null);
    experimentLog.render(state);
    experimentLog.output.visible = showExperimentLog;
    rightPane.render(state, showRightPane, rightWidth);
    overlay.render(
      paneFallback === null
        ? state
        : {...state, overlay: {kind: 'detail' as const, content: paneFallback.content}},
    );
    themePicker.render(state);
    chat.render(state);
    conversationActivityBar.render(state, !showLog);
    // One cursor, three places it can be. The modal owns it while it is open;
    // otherwise it belongs to whichever input the pane focus points at.
    const target: FocusTarget = state.chatOpen ? 'modal' : chatInputFocused ? 'chat' : 'command';
    if (target !== focusTarget) {
      focusTarget = target;
      if (target === 'modal') chat.focus();
      else if (target === 'chat') chatPane.focusComposer();
      else commandInput.focus();
    }
    releasePreviousStyle?.();
  };
  const unbindKeys = bindKeybindings(renderer, controller, viewport, clipboard, {
    completeInput: () => commandInput.completeSuggestion(),
    navigateSuggestions: direction => commandInput.navigateSuggestions(direction),
    // Routed to whichever chat presentation is currently on screen: the
    // modal wins while it is open, otherwise the docked pane.
    navigateChatSuggestions: direction =>
      controller.state.chatOpen
        ? chat.navigateSuggestions(direction)
        : chatPane.navigateSuggestions(direction),
    completeChatInput: () =>
      controller.state.chatOpen ? chat.completeSuggestion() : chatPane.completeSuggestion(),
    // Enter belongs to a pane only when nothing is typed anywhere. Asking which
    // box has the cursor is not enough: a question waiting in the other box is
    // still a question, and Enter must never discard it to open a hypothesis.
    inputIsEmpty: () =>
      commandInput.isEmpty() && chatPane.isComposerEmpty() && chat.isComposerEmpty(),
    closeChat: () => controller.closeChat(),
    toggleLatestPrompt: () => conversation.toggleLatestPrompt(),
    toggleSelectedTool: () => conversation.toggleSelectedTool(),
    revealOlderEntries: revealOlderEntries,
    revealSelectedEntry: () => {
      const card = conversation.selectedCard();
      if (card !== null) viewport.scrollChildIntoView(card.id);
    },
    selectNextAgent: () => controller.selectNextAgent(),
    selectPreviousAgent: () => controller.selectPreviousAgent(),
    selectNextRound: () => controller.selectNextRound(),
    selectPreviousRound: () => controller.selectPreviousRound(),
    toggleTodos: () => controller.toggleTodos(),
    scrollRightPane: delta => rightPane.scrollBy(delta),
    scrollChatPane: delta => chatPane.scrollBy(delta),
    scrollExperimentDetail: delta => experimentLog.scrollBy(delta),
    scrollErrorBanner: delta => errorBanner.scrollBy(delta),
    scrollOverlay: delta => overlay.scrollBy(delta),
    clearTransientStatus: () => {
      if (transientStatus === null) return;
      transientStatus = null;
      help.content = renderedKeyHelp;
    },
    showClipboardStatus: result => {
      transientStatus =
        result === 'copied'
          ? 'Copied selected text · Ctrl+C exits when no text is selected'
          : 'Copy unavailable (OSC52) · selection kept · use your terminal copy command';
      help.content = transientStatus;
    },
  });
  // Pane widths come from the terminal, so a resize has to redraw even though
  // no state changed.
  const onResize = (): void => render(lastState);
  renderer.on('resize', onResize);
  const unsubscribe = controller.subscribe(render);

  return {
    destroy(): void {
      renderer.off('resize', onResize);
      unsubscribe();
      unbindKeys();
      commandInput.destroy();
      conversationActivityBar.destroy();
      roundStrip.destroy();
      agentMap.destroy();
      experimentLog.destroy();
      root.destroyRecursively();
      markdownStyle.destroy();
    },
  };
}
