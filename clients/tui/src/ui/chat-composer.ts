import {
  BoxRenderable,
  type CliRenderer,
  type KeyEvent,
  TextareaRenderable,
  TextRenderable,
} from '@opentui/core';
import {suggestChatSlashCommands} from '../commands.js';
import type {ChatMenuRow, SessionState} from '../session-model.js';
import {applyPaneFocus, paneBorderColor, paneBorderStyle, paneTitle} from './focus.js';
import {SuggestionMenu} from './suggestion-menu.js';
import type {Theme} from './theme.js';

const MIN_EDITOR_ROWS = 1;
const MAX_EDITOR_ROWS = 6;
const COMPOSER_CHROME = 3;
const EDITOR_HORIZONTAL_CHROME = 4;
/** Rows the menu shows at once before it scrolls its selection into view. */
const MAX_MENU_ROWS = 10;
const MENU_CHROME = 2;
const COMPOSER_TITLE = 'Message';
const PENDING_COMPOSER_TITLE = `${COMPOSER_TITLE} · awaiting agent`;

class ChatTextareaRenderable extends TextareaRenderable {
  override handleKeyPress(key: KeyEvent): boolean {
    if (key.name === 'return' || key.name === 'kpenter' || key.name === 'linefeed') {
      if (key.shift) return this.newLine();
      if (!key.ctrl && !key.meta && !key.super && !key.hyper) return this.submit();
    }
    return super.handleKeyPress(key);
  }
}

/**
 * Draft shared by the docked and modal presentations of experiment chat. The
 * typed-command suggestion menu lives here too, alongside the text it is
 * derived from, so switching which presentation is on screen never resets or
 * duplicates the highlighted suggestion (see the multi-parent note on
 * `ChatComposerView` below).
 */
export interface ChatDraft {
  value: string;
  readonly suggestions: SuggestionMenu;
}

export function createChatDraft(): ChatDraft {
  return {value: '', suggestions: new SuggestionMenu()};
}

/**
 * The single experiment-chat composer used by every chat presentation.
 *
 * OpenTUI renderables cannot have two parents, so the dock and modal each own
 * an instance while sharing the draft above. Visibility transitions copy the
 * authoritative draft into the newly visible editor. This keeps resizing from
 * losing a partially written question without coupling either view to the
 * other's render tree.
 */
export class ChatComposerView {
  readonly output: BoxRenderable;
  /**
   * The composer's inline menu: chat commands as they are typed, and the row
   * selection `/model` and `/resume` open. It is a sibling of `output` rather
   * than a screen-level dialog, mounted by whichever chat surface owns this
   * composer, so the list rises out of the box it belongs to. This mirrors the
   * command input's suggestion list on the other side of the screen.
   */
  readonly menu: BoxRenderable;
  readonly #box: BoxRenderable;
  readonly #editor: ChatTextareaRenderable;
  readonly #hint: TextRenderable;
  readonly #menuList: TextRenderable;
  #availableWidth = 1;
  #focused = false;
  /** Whether the agent still owes an answer, which the title says. */
  #pending = false;
  #theme: Theme;
  #renderedMenu: string | null = null;
  #lastState: SessionState | null = null;

  constructor(
    renderer: CliRenderer,
    private readonly draft: ChatDraft,
    private readonly onSubmit: (value: string) => void,
    theme: Theme,
    id: string,
    private readonly onFocusRequest: () => void = () => {},
  ) {
    this.#theme = theme;
    this.output = new BoxRenderable(renderer, {
      id: `${id}-composer`,
      width: '100%',
      height: COMPOSER_CHROME + MIN_EDITOR_ROWS,
      flexDirection: 'column',
      flexShrink: 0,
    });
    this.#box = new BoxRenderable(renderer, {
      id: `${id}-composer-box`,
      width: '100%',
      height: MIN_EDITOR_ROWS + 2,
      border: true,
      borderStyle: paneBorderStyle(false),
      borderColor: paneBorderColor(theme, false),
      title: paneTitle(COMPOSER_TITLE, false),
      paddingLeft: 1,
      paddingRight: 1,
      onMouseUp: this.onFocusRequest,
    });
    this.#editor = new ChatTextareaRenderable(renderer, {
      id: `${id}-composer-editor`,
      width: '100%',
      height: MIN_EDITOR_ROWS,
      initialValue: draft.value,
      placeholder: 'Ask about this experiment',
      wrapMode: 'word',
      textColor: theme.textStrong,
      focusedTextColor: theme.textStrong,
      onMouseUp: this.onFocusRequest,
      onContentChange: () => {
        this.draft.value = this.#editor.plainText;
        this.#resize();
        this.#syncSuggestions();
        // Typing does not go through the controller, so the command
        // suggestions have to follow the draft rather than a state change.
        if (this.#lastState !== null) this.renderMenu(this.#lastState);
      },
      onSubmit: () => this.#submit(),
    });
    this.#hint = new TextRenderable(renderer, {
      id: `${id}-composer-hint`,
      width: '100%',
      height: 1,
      wrapMode: 'none',
      truncate: true,
      fg: theme.textSubtle,
      content: 'Enter: send · Shift+Enter: newline',
    });
    this.menu = new BoxRenderable(renderer, {
      id: `${id}-composer-menu`,
      position: 'absolute',
      // Anchored directly above the composer, which is the bottom of whichever
      // chat surface mounts it. Kept in step with the editor's height below.
      bottom: COMPOSER_CHROME + MIN_EDITOR_ROWS,
      left: 0,
      width: '100%',
      height: 3,
      visible: false,
      zIndex: 5,
      border: true,
      borderStyle: 'rounded',
      borderColor: theme.border,
      backgroundColor: theme.elevatedSurface,
      paddingLeft: 1,
      paddingRight: 1,
    });
    this.#menuList = new TextRenderable(renderer, {
      id: `${id}-composer-menu-list`,
      width: '100%',
      height: 1,
      fg: theme.textPrimary,
      wrapMode: 'none',
      truncate: true,
      content: '',
    });
    this.menu.add(this.#menuList);
    this.#box.add(this.#editor);
    this.output.add(this.#box);
    this.output.add(this.#hint);
  }

  /**
   * Fills the inline menu. The controller-owned menu wins over the typed
   * command suggestions: once `/model` is submitted, the list is a selection
   * rather than a completion.
   */
  renderMenu(state: SessionState): void {
    this.#lastState = state;
    const lines = this.#menuLines(state);
    const fingerprint = lines === null ? null : lines.join('\n');
    if (this.#renderedMenu === fingerprint) return;
    this.#renderedMenu = fingerprint;
    this.menu.visible = lines !== null;
    if (lines === null) return;
    this.#menuList.content = lines.join('\n');
    this.#menuList.height = Math.max(1, lines.length);
    this.menu.height = lines.length + MENU_CHROME;
  }

  /** The menu's rows, or null when nothing should be on screen. */
  #menuLines(state: SessionState): string[] | null {
    const menu = state.chatMenu;
    if (menu !== null) {
      const rows = visibleRows(menu.rows, menu.selected);
      return [
        menu.title,
        ...rows.map(({row, index}) => menuRowText(row, index === menu.selected, menu.customModels)),
      ];
    }
    if (!this.draft.suggestions.visible) return null;
    return this.draft.suggestions.renderLines(this.draft.value.trim());
  }

  /**
   * Highlights, navigates, and Tab-completes the typed-command suggestions
   * the same way the command bar does. Callers gate these on the menu
   * actually showing suggestions (rather than the controller-owned
   * `ChatMenu`), since both share the one inline list.
   */
  navigateSuggestions(direction: 1 | -1): boolean {
    if (!this.draft.suggestions.navigate(direction)) return false;
    if (this.#lastState !== null) this.renderMenu(this.#lastState);
    return true;
  }

  completeSuggestion(): boolean {
    const value = this.draft.suggestions.complete(this.draft.value);
    if (value === null) return false;
    this.#editor.setText(value);
    this.#editor.cursorOffset = value.length;
    return true;
  }

  /** Recomputes the typed-command matches for the draft's current text. */
  #syncSuggestions(): void {
    this.draft.suggestions.setMatches(suggestChatSlashCommands(this.draft.value.trim()));
  }

  /** Makes this editor authoritative when its presentation becomes visible. */
  activate(availableWidth: number, focused: boolean, pending: boolean): void {
    this.#availableWidth = Math.max(1, availableWidth);
    if (this.#editor.plainText !== this.draft.value) {
      this.#editor.setText(this.draft.value);
      this.#syncSuggestions();
    }
    this.#pending = pending;
    this.setFocused(focused);
    this.#hint.content = pending
      ? 'Awaiting the agent · Enter: queue follow-up'
      : focused
        ? 'Enter: send · Shift+Enter: newline'
        : 'Ctrl+W to type here';
    this.#resize();
  }

  isEmpty(): boolean {
    return this.draft.value.trim() === '';
  }

  focus(): void {
    this.#editor.focus();
  }

  setFocused(focused: boolean): void {
    this.#focused = focused;
    this.#applyFocus();
  }

  #applyFocus(): void {
    const label = this.#pending ? PENDING_COMPOSER_TITLE : COMPOSER_TITLE;
    applyPaneFocus(this.#box, this.#theme, label, this.#focused);
  }

  applyTheme(theme: Theme): void {
    this.#theme = theme;
    this.#applyFocus();
    this.#editor.textColor = theme.textStrong;
    this.#editor.focusedTextColor = theme.textStrong;
    this.#hint.fg = theme.textSubtle;
    this.menu.borderColor = theme.border;
    this.menu.backgroundColor = theme.elevatedSurface;
    this.#menuList.fg = theme.textPrimary;
  }

  #resize(): void {
    const contentWidth = Math.max(1, this.#availableWidth - EDITOR_HORIZONTAL_CHROME);
    const rows = Math.min(
      MAX_EDITOR_ROWS,
      Math.max(MIN_EDITOR_ROWS, wrappedRows(this.draft.value, contentWidth)),
    );
    this.#editor.height = rows;
    this.#box.height = rows + 2;
    this.output.height = rows + COMPOSER_CHROME;
    // The menu sits on top of the composer, so it moves with it.
    this.menu.bottom = this.output.height;
  }

  #submit(): void {
    const value = this.draft.value;
    if (!value.trim()) return;
    this.draft.value = '';
    this.#editor.clear();
    this.#resize();
    this.#syncSuggestions();
    this.onSubmit(value);
  }
}

/**
 * Keeps the highlighted row on screen without scrolling machinery: a window of
 * at most `MAX_MENU_ROWS` rows that always contains the selection.
 */
function visibleRows(
  rows: readonly ChatMenuRow[],
  selected: number,
): {row: ChatMenuRow; index: number}[] {
  const indexed = rows.map((row, index) => ({row, index}));
  if (indexed.length <= MAX_MENU_ROWS) return indexed;
  const start = Math.min(
    Math.max(0, selected - Math.floor(MAX_MENU_ROWS / 2)),
    indexed.length - MAX_MENU_ROWS,
  );
  return indexed.slice(start, start + MAX_MENU_ROWS);
}

function menuRowText(
  row: ChatMenuRow,
  selected: boolean,
  customModels: Record<string, string>,
): string {
  const marker = selected ? '›' : ' ';
  if (row.kind === 'header') return `  ${row.label}`;
  if (row.kind === 'note') return `  ${row.label}`;
  if (row.kind === 'thread') return `${marker} ${row.label} · ${row.detail}`;
  if (row.kind === 'model') return `${marker}   ${row.label}`;
  const typed = customModels[row.provider] ?? '';
  // A cursor bar marks the free-text entry as somewhere to type, the same
  // affordance the wizard's model step used.
  return `${marker}   ${typed === '' ? row.label : typed}${selected ? '▏' : ''}`;
}

function wrappedRows(value: string, width: number): number {
  if (value.length === 0) return 1;
  return value
    .split('\n')
    .reduce((rows, line) => rows + Math.max(1, Math.ceil([...line].length / width)), 0);
}
