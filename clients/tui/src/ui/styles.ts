import {
  CodeRenderable,
  createMarkdownCodeBlockRenderer,
  type MarkdownOptions,
  type MarkdownTableOptions,
  SyntaxStyle,
} from '@opentui/core';
import type {ConversationEntry} from '../session-model.js';
import type {ConversationRole, ConversationRoleColors, Theme} from './theme.js';

export type EntryPalette = ConversationRoleColors;

type MarkdownRenderNode = NonNullable<MarkdownOptions['renderNode']>;

/**
 * The surface any code draws on, whatever produced it.
 *
 * Two consumers decide it, so it has to be one decision. A top-level fence is
 * a `CodeRenderable` the node override restyles. An inline span, and a fence
 * nested in a list, arrive instead as `markup.raw*` captures inside a
 * coalesced markdown block and take their colors from the syntax style. Naming
 * the pair twice lets a fence change color depending on where it sits.
 */
function codeSurface({markdown}: Theme): {fg: string; bg: string} {
  return {fg: markdown.code, bg: markdown.codeBackground};
}

export function createMarkdownStyle(theme: Theme): SyntaxStyle {
  const {markdown} = theme;
  // Style names are the markup.* capture groups the markdown renderer emits.
  // Lookup tries the exact capture and then only its first dotted segment, so
  // the numbered heading captures each need their own entry: a plain
  // "heading" entry is never consulted, which left every markdown color
  // except default unused.
  const heading = {fg: markdown.heading, bold: true};
  const code = codeSurface(theme);
  return SyntaxStyle.fromStyles({
    default: {fg: markdown.default},
    'markup.heading': heading,
    'markup.heading.1': heading,
    'markup.heading.2': heading,
    'markup.heading.3': heading,
    'markup.heading.4': heading,
    'markup.heading.5': heading,
    'markup.heading.6': heading,
    'markup.strong': {fg: markdown.strong, bold: true},
    'markup.italic': {fg: markdown.em, italic: true},
    'markup.raw': code,
    'markup.raw.block': code,
    'markup.link': {fg: markdown.link, underline: true},
    'markup.link.url': {fg: markdown.link, underline: true},
    'markup.link.label': {fg: markdown.link},
    'markup.quote': {fg: markdown.blockquote, italic: true},
  });
}

/**
 * Table presentation for markdown blocks.
 *
 * Every option here is one the renderer would otherwise default badly for
 * inside a transcript card. Its own defaults size columns to the available
 * width, which pads a three-character cell out to a quarter of the pane, and
 * they draw the border in a fixed grey that ignores the theme. `content`
 * sizing plus a balanced fitter is what keeps a wide table inside a card that
 * is already inset by the card border and its padding.
 */
export function createMarkdownTableOptions(theme: Theme): MarkdownTableOptions {
  return {
    style: 'grid',
    widthMode: 'content',
    columnFitter: 'balanced',
    wrapMode: 'word',
    cellPaddingX: 1,
    cellPaddingY: 0,
    borders: true,
    borderStyle: 'rounded',
    borderColor: theme.border,
  };
}

/**
 * Declares that a `renderNode` only ever replaces fenced code blocks.
 *
 * `MarkdownRenderable` coalesces runs of prose into one renderable per block,
 * but only while it knows the override cannot claim an ordinary token. Any
 * other `renderNode` drops it to one renderable per token, so a message of a
 * hundred paragraphs becomes a hundred native renderables. The declaration is
 * a marker the renderer stamps on what `createMarkdownCodeBlockRenderer`
 * returns; 0.4.3 keeps it off `MarkdownOptions`, so copying it from an empty
 * renderer is the only way to make the claim without naming a private field.
 *
 * The factory itself is not usable directly here: it dispatches per resolved
 * language and drops fences whose info string names none, which is most of
 * them in a transcript.
 */
function asCodeBlockOnly(renderNode: MarkdownRenderNode): MarkdownRenderNode {
  // The factory's return type is the optional `renderNode` field, but it
  // always returns a function.
  const marker = createMarkdownCodeBlockRenderer({}) as MarkdownRenderNode;
  return Object.assign(renderNode, marker);
}

/**
 * Draws fenced code blocks on the theme's code surface.
 *
 * Inline code picks up `markup.raw` from the syntax style, but a fenced block
 * is rendered by `CodeRenderable`, which colors text from tree-sitter captures
 * for the block's own language. The package ships grammars for markdown,
 * JavaScript, TypeScript and Zig, so the info string of a transcript fence
 * usually names one it has no grammar for and none is cached locally.
 * Highlighting then falls back to plain text and the block arrives with no
 * foreground and no background: visually identical to the prose around it.
 * Restyling the default block gives it a code surface whether or not a grammar
 * is ever available, and highlighting still applies on top when one is.
 *
 * The default block is restyled rather than replaced. A replacement would
 * discard the margins, streaming mode, concealment, tree-sitter client, and
 * info-string normalization the renderer put on it, and the renderer would
 * stop tracking it: the visible symptom is a fenced block sitting flush
 * against the paragraph after it.
 *
 * Only top-level fences reach this. A fence nested in a list is part of the
 * block the renderer coalesces its list into, so it is markdown source inside
 * a markdown block, and `codeSurface` reaches it through `markup.raw.block`
 * instead. `createListChildRenderable`, which would build a `CodeRenderable`
 * for it without consulting any override, is only reachable in
 * `internalBlockMode: 'top-level'`, which the transcript does not use.
 */
export function createMarkdownCodeRenderer(theme: Theme): MarkdownRenderNode {
  const {fg, bg} = codeSurface(theme);
  return asCodeBlockOnly((token, context) => {
    if (token.type !== 'code') return undefined;
    const block = context.defaultRender();
    if (!(block instanceof CodeRenderable)) return block;
    block.fg = fg;
    block.bg = bg;
    // The default suppresses the plain-text draw while streaming and waits for
    // highlighting to supply styled chunks instead. With no grammar available
    // that wait resolves to plain text anyway, so the block would just be blank
    // until it did.
    block.drawUnstyledText = true;
    return block;
  });
}

export function conversationRole(entry: ConversationEntry): ConversationRole {
  if (entry.tone === 'failure') return 'failure';
  if (entry.tone === 'success') return 'success';
  if (entry.kind === 'assistant') return 'assistant';
  if (entry.kind === 'user') return 'user';
  if (entry.kind === 'prompt') return 'prompt';
  // An agent narrating its own work is analysis whichever channel carried it:
  // the diagnostic channel is where most backends put that narration, and
  // slate-on-slate buried it. Tool turns keep the neutral surface.
  if (entry.kind === 'analysis' || entry.kind === 'diagnostic') return 'analysis';
  if (entry.kind === 'tool' || entry.kind === 'subprocess') return 'tool';
  return 'neutral';
}

export function entryPalette(entry: ConversationEntry, theme: Theme): EntryPalette {
  return theme.conversation[conversationRole(entry)];
}
