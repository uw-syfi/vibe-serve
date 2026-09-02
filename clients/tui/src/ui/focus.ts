import type {BorderStyle, BoxRenderable} from '@opentui/core';
import type {Theme} from './theme.js';

/**
 * How a pane says it is the one taking keys.
 *
 * Three channels, in priority order: a marker in a reserved title gutter, the
 * border style, then colour. Colour is last because it cannot carry the signal
 * on its own. `borderFocus` and `border` sit within 1.5x of each other in five
 * of the eight built-in themes, and the high-contrast pair collapses them by
 * construction: those palettes are deliberately tiny, so there is no shade to
 * move to. The first two channels hold in any palette, and in a terminal with
 * no colour at all.
 *
 * This module owns how focus looks, never which surface has it: `focusedPane`
 * is that authority, and each pane compares its own `PaneId` against it. A
 * surface that is not a pane never wears the treatment. The command box is the
 * case worth naming: it sits under the pane column and is shared by every pane
 * in it rather than being one of them, so it takes the resting title, frame and
 * colour and no focused variant. It calls in only to keep its label at the same
 * column as the panes above it. Lighting it made the marked pane ambiguous,
 * which is #433.
 */

/** Marks the title of the pane that currently takes keys. */
const FOCUS_MARKER = '▸';

/** Occupies the marker's cell while a pane is at rest. */
const MARKER_GUTTER = ' ';

const RESTING_BORDER: BorderStyle = 'rounded';

/**
 * Heavier ink on the same single-cell frame, so a focused pane reads as louder
 * rather than merely different. `double` would also fit the cell, but two thin
 * strokes can blur into something lighter than one at small type sizes, which
 * would invert this channel the way `high-contrast-light` once inverted colour.
 */
const FOCUSED_BORDER: BorderStyle = 'heavy';

/**
 * A pane title with the marker's cell reserved whether or not the pane holds
 * focus.
 *
 * Prepending the marker instead slides the label two columns right, so the eye,
 * which uses the title's left edge as a landmark, reads a focus change as the
 * layout moving. Reserving the cell turns it into a glyph swap at a fixed
 * column, and keeps a narrow pane's title truncating identically either way.
 */
export function paneTitle(label: string, focused: boolean): string {
  // Symmetric: the gutter and the lead space together are two cells, and the
  // label gets two on the other side, so the label sits centred in its own
  // inset rather than pushed against the trailing edge.
  return ` ${focused ? FOCUS_MARKER : MARKER_GUTTER} ${label}   `;
}

/**
 * The frame a pane draws.
 *
 * Both styles are one cell per side, and OpenTUI invalidates only the raster
 * when `borderStyle` changes, so a focus change cannot move the layout. A
 * border of a different *width* would, which is why this channel is a style
 * swap and not a thicker frame.
 */
export function paneBorderStyle(focused: boolean): BorderStyle {
  return focused ? FOCUSED_BORDER : RESTING_BORDER;
}

/** Reinforces the two channels above, as far as a given palette allows. */
export function paneBorderColor(theme: Theme, focused: boolean): string {
  return focused ? theme.borderFocus : theme.border;
}

/** Applies every focus channel to one pane frame. */
export function applyPaneFocus(
  box: BoxRenderable,
  theme: Theme,
  label: string,
  focused: boolean,
): void {
  box.title = paneTitle(label, focused);
  box.borderStyle = paneBorderStyle(focused);
  box.borderColor = paneBorderColor(theme, focused);
}
