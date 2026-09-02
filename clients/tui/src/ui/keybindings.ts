import type {CliRenderer, KeyEvent, ScrollBoxRenderable} from '@opentui/core';
import type {SessionController} from '../session-controller.js';
import {chatPaneFocused, chatPaneVisible, experimentLogVisible} from '../session-model.js';
import type {ClipboardCopyResult, SelectionClipboard} from './clipboard.js';

export interface KeybindingActions {
  completeInput(): boolean;
  navigateSuggestions(direction: 1 | -1): boolean;
  /** Navigates the chat composer's own typed-command suggestions, docked or modal. */
  navigateChatSuggestions(direction: 1 | -1): boolean;
  /** Tab-completes the chat composer's highlighted typed-command suggestion. */
  completeChatInput(): boolean;
  inputIsEmpty(): boolean;
  closeChat(): void;
  toggleLatestPrompt(): void;
  toggleSelectedTool(): boolean;
  /** Brings the entry the cursor moved to into view. */
  revealSelectedEntry(): void;
  /** Materializes the next block of conversation history above the window. */
  revealOlderEntries(): void;
  selectNextAgent(): void;
  selectPreviousAgent(): void;
  selectNextRound(): void;
  selectPreviousRound(): void;
  toggleTodos(): void;
  scrollRightPane(delta: number): void;
  scrollChatPane(delta: number): void;
  scrollExperimentDetail(delta: number): void;
  scrollErrorBanner(delta: number): void;
  scrollOverlay(delta: number): void;
  clearTransientStatus(): void;
  showClipboardStatus(result: Exclude<ClipboardCopyResult, 'no-selection'>): void;
}

export function bindKeybindings(
  renderer: CliRenderer,
  controller: SessionController,
  viewport: ScrollBoxRenderable,
  clipboard: SelectionClipboard,
  actions: KeybindingActions,
): () => void {
  const onKey = (key: KeyEvent): void => {
    if (key.ctrl && !key.shift && key.name === 'c') {
      key.preventDefault();
      const result = clipboard.copySelection();
      if (result === 'no-selection') renderer.destroy();
      else actions.showClipboardStatus(result);
      return;
    }
    actions.clearTransientStatus();
    if (
      key.name === 'f4' &&
      controller.state.chatOpen === false &&
      controller.state.overlay === null &&
      controller.state.themePicker === null &&
      controller.state.chatMenu === null
    ) {
      controller.togglePaneZoom();
      key.preventDefault();
      return;
    }
    if (
      controller.state.errorBanner !== null &&
      key.ctrl &&
      (key.name === 'pageup' || key.name === 'pagedown')
    ) {
      actions.scrollErrorBanner(key.name === 'pageup' ? -1 : 1);
      key.preventDefault();
      return;
    }
    if (controller.state.errorBanner !== null && key.name === 'escape') {
      controller.dismissErrorBanner();
      key.preventDefault();
      return;
    }
    if (
      key.ctrl &&
      key.name === 'w' &&
      (controller.state.layout.right !== null || chatPaneVisible(controller.state))
    ) {
      controller.cyclePaneFocus();
      key.preventDefault();
      return;
    }
    // The composer's inline menu owns the keys while it is open. On a custom
    // model entry that includes ordinary typing, which must never leak into
    // the composer underneath; anywhere else typing dismisses the menu and
    // goes back to writing a question, so the keystroke is left alone.
    const chatMenu = controller.state.chatMenu;
    if (chatMenu !== null) {
      const onCustomEntry = chatMenu.rows[chatMenu.selected]?.kind === 'custom';
      if (key.name === 'up') controller.moveChatMenuSelection(-1);
      else if (key.name === 'down') controller.moveChatMenuSelection(1);
      else if (key.name === 'escape') controller.closeChatMenu();
      else if (key.name === 'return' || key.name === 'enter' || key.name === 'kpenter') {
        void controller.confirmChatMenu();
      } else if (onCustomEntry && key.name === 'backspace') {
        controller.backspaceChatMenuCustomModel();
      } else if (onCustomEntry && isPrintable(key)) {
        controller.typeChatMenuCustomModel(key.sequence);
      } else {
        if (isPrintable(key)) controller.closeChatMenu();
        return;
      }
      key.preventDefault();
      return;
    }
    // The focused pane takes the scroll keys. Everything else the chat or the
    // transcript would normally handle is left alone.
    if (
      controller.state.layout.focus === 'right' &&
      controller.state.layout.right !== null &&
      (key.name === 'pageup' || key.name === 'pagedown' || key.name === 'escape')
    ) {
      if (key.name === 'escape') controller.closeOverlays();
      else actions.scrollRightPane(key.name === 'pageup' ? -1 : 1);
      key.preventDefault();
      return;
    }
    // Modal state is authoritative: the theme picker, the modal chat, and any
    // overlay must contain input before the focused docked chat runs. Otherwise
    // a modal opened while the docked chat has focus would leak printable keys
    // into the hidden composer, let Up/Down drive chat suggestions, and route
    // Escape to the left pane instead of closing the modal.
    if (controller.state.themePicker !== null) {
      if (key.name === 'up') controller.moveThemeSelection(-1);
      else if (key.name === 'down') controller.moveThemeSelection(1);
      else if (key.name === 'pageup') controller.moveThemeSelection(-10);
      else if (key.name === 'pagedown') controller.moveThemeSelection(10);
      else if (key.name === 'escape') controller.closeThemePicker();
      else if (key.name === 'return' || key.name === 'enter') controller.applySelectedTheme();
      // The picker is modal: keys it does not use are swallowed here so they
      // cannot move panes or type into the still-focused input behind it.
      key.preventDefault();
      return;
    }
    if (controller.state.chatOpen) {
      if (key.name === 'escape') {
        if (controller.state.layout.right !== null) controller.closeOverlays();
        else actions.closeChat();
        key.preventDefault();
        return;
      }
      // Same suggestion-menu priority as the docked chat below.
      if (key.name === 'up' || key.name === 'down') {
        if (actions.navigateChatSuggestions(key.name === 'up' ? -1 : 1)) key.preventDefault();
        return;
      }
      if (key.name === 'tab' && !key.shift) {
        if (actions.completeChatInput()) key.preventDefault();
        return;
      }
      return;
    }
    if (controller.state.overlay !== null) {
      if (key.name === 'escape') {
        controller.live();
        viewport.scrollTo(viewport.scrollHeight);
      }
      // The overlay is modal: everything it does not handle is swallowed so
      // keys cannot reach the panes or the hidden command input behind it.
      key.preventDefault();
      return;
    }
    if (chatPaneFocused(controller.state)) {
      if (key.name === 'pageup' || key.name === 'pagedown' || key.name === 'escape') {
        if (key.name === 'escape') controller.focusPane('left');
        else actions.scrollChatPane(key.name === 'pageup' ? -1 : 1);
        key.preventDefault();
        return;
      }
      // The typed-command suggestions take Up/Down/Tab only while they are
      // showing; otherwise the keys fall through to the editor underneath
      // (multiline cursor movement, and Tab's ordinary no-op).
      if (key.name === 'up' || key.name === 'down') {
        if (actions.navigateChatSuggestions(key.name === 'up' ? -1 : 1)) key.preventDefault();
        return;
      }
      if (key.name === 'tab' && !key.shift) {
        if (actions.completeChatInput()) key.preventDefault();
        return;
      }
      return;
    }
    if (controller.state.themePicker !== null) {
      if (key.name === 'up') controller.moveThemeSelection(-1);
      else if (key.name === 'down') controller.moveThemeSelection(1);
      else if (key.name === 'pageup') controller.moveThemeSelection(-10);
      else if (key.name === 'pagedown') controller.moveThemeSelection(10);
      else if (key.name === 'escape') controller.closeThemePicker();
      else if (key.name === 'return' || key.name === 'enter') {
        if (!actions.inputIsEmpty()) return;
        controller.applySelectedTheme();
      } else return;
      key.preventDefault();
      return;
    }
    if (controller.state.chatOpen) {
      if (key.name === 'escape') {
        if (controller.state.layout.right !== null) controller.closeOverlays();
        else actions.closeChat();
        key.preventDefault();
        return;
      }
      // Same suggestion-menu priority as the docked chat above.
      if (key.name === 'up' || key.name === 'down') {
        if (actions.navigateChatSuggestions(key.name === 'up' ? -1 : 1)) key.preventDefault();
        return;
      }
      if (key.name === 'tab' && !key.shift) {
        if (actions.completeChatInput()) key.preventDefault();
        return;
      }
      return;
    }
    if (controller.state.overlay !== null) {
      if (key.name === 'escape') {
        controller.live();
        viewport.scrollTo(viewport.scrollHeight);
        key.preventDefault();
        return;
      }
      if (key.name === 'pageup') {
        actions.scrollOverlay(-1);
        key.preventDefault();
        return;
      }
      if (key.name === 'pagedown') {
        actions.scrollOverlay(1);
        key.preventDefault();
        return;
      }
    }
    // The experiment surface owns navigation while it is on screen. The index
    // opens a hypothesis summary; that summary selects and opens one round.
    // The input keeps priority over Enter so a typed command is never lost.
    if (experimentLogVisible(controller.state)) {
      const detailOpen = controller.state.hypothesisDetail !== null;
      if (key.name === 'escape' && detailOpen) controller.leaveHypothesisDetail();
      else if (key.name === 'up') {
        if (detailOpen) controller.moveHypothesisRoundSelection(-1);
        else if (!actions.navigateSuggestions(-1)) controller.moveExperimentSelection(-1);
      } else if (key.name === 'down') {
        if (detailOpen) controller.moveHypothesisRoundSelection(1);
        else if (!actions.navigateSuggestions(1)) controller.moveExperimentSelection(1);
      } else if (key.name === 'pageup') {
        if (detailOpen) actions.scrollExperimentDetail(-1);
        else controller.moveExperimentSelection(-10);
      } else if (key.name === 'pagedown') {
        if (detailOpen) actions.scrollExperimentDetail(1);
        else controller.moveExperimentSelection(10);
      } else if (key.name === 'tab' && !key.shift) {
        // The table has no agent strip to cycle through, so Tab belongs to the
        // suggestion it would otherwise complete, or nothing at all.
        if (!actions.completeInput()) return;
      } else if (key.name === 'return' || key.name === 'enter') {
        // A typed command belongs to the input; let its own handler run it so
        // one Enter is enough. An overlay is in front of the table, so Enter
        // behind it must not move the operator somewhere they cannot see.
        if (!actions.inputIsEmpty()) return;
        if (controller.state.overlay === null) controller.enterExperimentDrilldown();
      } else return;
      key.preventDefault();
      return;
    }
    if (key.name === 'escape' && controller.state.hypothesisScope !== null) {
      if (controller.state.selectedEntryId !== null) controller.clearEntrySelection();
      else if (controller.state.selectedAgentKind !== null) controller.clearAgentSelection();
      else controller.leaveExperimentDrilldown();
      key.preventDefault();
      return;
    }
    if ((key.ctrl && key.name === 'p') || key.name === 'f3') {
      actions.toggleLatestPrompt();
      key.preventDefault();
      return;
    }
    if ((key.ctrl && key.name === 't') || key.name === 'f2') {
      actions.toggleTodos();
      key.preventDefault();
      return;
    }
    if (controller.state.todosExpanded) {
      if (key.name === 'up' || key.name === 'down') {
        controller.selectNextTodo(key.name === 'down' ? 1 : -1);
        key.preventDefault();
        return;
      }
      if (key.name === 'escape') {
        controller.toggleTodos();
        key.preventDefault();
        return;
      }
    }
    // Like Enter above, pane focus and round navigation yield to a typed
    // command: cursor keys and brackets belong to a non-empty input.
    if ((key.name === 'left' || key.name === 'right') && actions.inputIsEmpty()) {
      controller.focusRound(key.name === 'left' ? 'agents' : 'transcript');
      key.preventDefault();
      return;
    }
    if (key.name === 'up' || key.name === 'down') {
      if (!actions.navigateSuggestions(key.name === 'up' ? -1 : 1)) {
        if (controller.state.roundFocus === 'agents') {
          if (key.name === 'down') controller.selectNextAgent();
          else controller.selectPreviousAgent();
        } else {
          controller.selectNextEntry(key.name === 'down' ? 1 : -1);
          actions.revealSelectedEntry();
        }
      }
      key.preventDefault();
      return;
    }
    if (
      (key.name === 'return' || key.name === 'enter') &&
      controller.state.roundFocus === 'transcript' &&
      actions.inputIsEmpty() &&
      actions.toggleSelectedTool()
    ) {
      actions.revealSelectedEntry();
      key.preventDefault();
      return;
    }
    if (key.ctrl && key.name === 'l') {
      controller.live();
      viewport.scrollTo(viewport.scrollHeight);
      key.preventDefault();
      return;
    }
    if (key.name === 'tab' && !key.shift && actions.completeInput()) {
      key.preventDefault();
      return;
    }
    if (key.name === 'tab') {
      if (key.shift) actions.selectPreviousAgent();
      else actions.selectNextAgent();
      viewport.scrollTo(viewport.scrollHeight);
      key.preventDefault();
      return;
    }
    if (key.name === ']' && actions.inputIsEmpty()) {
      actions.selectNextRound();
      viewport.scrollTo(viewport.scrollHeight);
      key.preventDefault();
      return;
    }
    if (key.name === '[' && actions.inputIsEmpty()) {
      actions.selectPreviousRound();
      viewport.scrollTo(viewport.scrollHeight);
      key.preventDefault();
      return;
    }
    if (key.name === 'pageup') {
      actions.revealOlderEntries();
      viewport.scrollBy(-1, 'viewport');
    } else if (key.name === 'pagedown') viewport.scrollBy(1, 'viewport');
    else if (key.ctrl && key.name === 'up') {
      actions.revealOlderEntries();
      viewport.scrollBy(-1);
    } else if (key.ctrl && key.name === 'down') viewport.scrollBy(1);
    else if (key.name === 'home') {
      // Home reaches the top of what is rendered. On a windowed transcript that
      // is one further block of history per press, rather than one press
      // building every card a 20k-entry run has.
      actions.revealOlderEntries();
      viewport.scrollTo(0);
    } else if (key.name === 'end') viewport.scrollTo(viewport.scrollHeight);
    else return;
    key.preventDefault();
  };

  renderer.keyInput.on('keypress', onKey);
  return () => renderer.keyInput.off('keypress', onKey);
}

/** One typed character, as opposed to a chord or a control key. */
function isPrintable(key: KeyEvent): boolean {
  return (
    !key.ctrl &&
    !key.meta &&
    typeof key.sequence === 'string' &&
    key.sequence.length === 1 &&
    key.sequence >= ' ' &&
    key.sequence !== ''
  );
}
