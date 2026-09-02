import {afterEach, describe, expect, it} from 'bun:test';
import {
  CodeRenderable,
  type MarkdownOptions,
  MarkdownRenderable,
  rgbToHex,
  TextTableRenderable,
} from '@opentui/core';
import {createTestRenderer, MockTreeSitterClient} from '@opentui/core/testing';
import {
  createMarkdownCodeRenderer,
  createMarkdownStyle,
  createMarkdownTableOptions,
} from './styles.js';
import {resolveTheme, THEME_NAMES, type Theme, type ThemeName} from './theme.js';

const cleanup: Array<() => void> = [];

afterEach(() => {
  for (const destroy of cleanup.splice(0).reverse()) destroy();
});

/**
 * Which node override a fixture gets: the transcript's own, none at all, or an
 * arbitrary one the test supplies.
 */
type CodeRenderer = 'transcript' | 'none' | NonNullable<MarkdownOptions['renderNode']>;

interface MarkdownFixture {
  markdown: MarkdownRenderable;
  treeSitterClient: MockTreeSitterClient;
  layout: () => Promise<void>;
}

/**
 * A transcript markdown block, wired the way `ConversationView` wires one.
 *
 * `'none'` drops only the code renderer, which makes the renderer's own output
 * the comparison point for what the override is allowed to change. Streaming
 * is on because that is the transcript's default outside a terminal, and it is
 * the mode whose defaults the override has to correct.
 */
async function renderMarkdown(
  content: string,
  theme: Theme,
  codeRenderer: CodeRenderer = 'transcript',
): Promise<MarkdownFixture> {
  const {renderer, renderOnce} = await createTestRenderer({width: 60, height: 20});
  cleanup.push(() => renderer.destroy());
  const treeSitterClient = new MockTreeSitterClient();
  cleanup.push(() => void treeSitterClient.destroy());
  const renderNode =
    codeRenderer === 'transcript'
      ? createMarkdownCodeRenderer(theme)
      : codeRenderer === 'none'
        ? undefined
        : codeRenderer;
  const markdown = new MarkdownRenderable(renderer, {
    content,
    syntaxStyle: createMarkdownStyle(theme),
    conceal: true,
    streaming: true,
    treeSitterClient,
    tableOptions: createMarkdownTableOptions(theme),
    ...(renderNode === undefined ? {} : {renderNode}),
    width: '100%',
  });
  renderer.root.add(markdown);
  cleanup.push(() => markdown.destroyRecursively());
  return {markdown, treeSitterClient, layout: renderOnce};
}

/**
 * The block for one fence. Prose blocks are `CodeRenderable`s too (the
 * renderer highlights them as markdown), so they are told apart by content:
 * a prose block carries the fence markers, the fenced block only its body.
 */
function fencedBlock(markdown: MarkdownRenderable, body: string): CodeRenderable {
  const blocks = markdown
    .getChildren()
    .filter(child => child instanceof CodeRenderable && child.content === body);
  expect(blocks).toHaveLength(1);
  return blocks[0] as CodeRenderable;
}

/** Blank rows between the fenced block and the block after it. */
function gapAfterFence({markdown}: MarkdownFixture, body: string): number {
  const blocks = markdown.getChildren();
  const fence = fencedBlock(markdown, body);
  const next = blocks[blocks.indexOf(fence) + 1];
  expect(next).toBeDefined();
  return (next as (typeof blocks)[number]).y - (fence.y + fence.height);
}

describe('markdown table options', () => {
  it('sizes columns to content rather than to the pane', () => {
    // The renderer's own default expands columns to the available width, which
    // pads a three-character cell across a quarter of the transcript.
    const options = createMarkdownTableOptions(resolveTheme('dark'));
    expect(options.widthMode).toBe('content');
    expect(options.wrapMode).toBe('word');
  });

  it.each([...THEME_NAMES])('draws %s borders in the theme, not a fixed grey', name => {
    const theme = resolveTheme(name);
    const options = createMarkdownTableOptions(theme);
    expect(options.borderColor).toBe(theme.border);
    // The regression this guards: the renderer defaults to #888888 for table
    // borders regardless of theme, which is invisible on a light canvas.
    expect(options.borderColor).not.toBe('#888888');
  });
});

describe('markdown code blocks', () => {
  it.each<ThemeName>([
    'dark',
    'light',
  ])('draws a fenced block on the %s code surface', async name => {
    const theme = resolveTheme(name);
    const {markdown} = await renderMarkdown('```rust\nlet x = 1;\n```\n', theme);

    // Without this the block inherits the card's colors and reads as prose:
    // no grammars ship with the renderer, so highlighting is not available to
    // supply them.
    const code = fencedBlock(markdown, 'let x = 1;');
    expect(rgbToHex(code.fg)).toBe(theme.markdown.code);
    expect(rgbToHex(code.bg)).toBe(theme.markdown.codeBackground);
  });

  it('styles a fenced block that declares no language', async () => {
    const theme = resolveTheme('light');
    const {markdown} = await renderMarkdown('```\nplain text\n```\n', theme);

    const code = fencedBlock(markdown, 'plain text');
    expect(rgbToHex(code.fg)).toBe(theme.markdown.code);
    expect(rgbToHex(code.bg)).toBe(theme.markdown.codeBackground);
  });

  it('draws code text without waiting for a grammar that never arrives', async () => {
    const theme = resolveTheme('dark');
    const fence = '```rust\nlet x = 1;\n```\n';
    const {markdown} = await renderMarkdown(fence, theme);
    const {markdown: plain} = await renderMarkdown(fence, theme, 'none');

    // Streaming suppresses the plain-text draw until highlighting supplies
    // styled chunks. Nothing ever does here, so the block would stay blank.
    expect(fencedBlock(plain, 'let x = 1;').drawUnstyledText).toBe(false);
    expect(fencedBlock(markdown, 'let x = 1;').drawUnstyledText).toBe(true);
  });

  it('keeps the margin the renderer puts after a fenced block', async () => {
    const theme = resolveTheme('dark');
    const content = '```rust\nlet x = 1;\n```\n\nProse after the block.\n';
    const styled = await renderMarkdown(content, theme);
    const plain = await renderMarkdown(content, theme, 'none');
    await styled.layout();
    await plain.layout();

    // Replacing the block instead of restyling it drops it out of the
    // renderer's margin tracking, and it ends up flush against the paragraph.
    // `marginBottom` is write-only, so the gap is read off the laid-out rows.
    expect(gapAfterFence(plain, 'let x = 1;')).toBe(1);
    expect(gapAfterFence(styled, 'let x = 1;')).toBe(1);
  });

  it('styles the block the renderer built rather than a replacement', async () => {
    const theme = resolveTheme('dark');
    const {markdown, treeSitterClient} = await renderMarkdown('```rs\nlet x = 1;\n```\n', theme);

    const code = fencedBlock(markdown, 'let x = 1;');
    // A freshly constructed block would fall back to the process-wide client
    // and would take the info string verbatim instead of normalizing it.
    expect(code.treeSitterClient).toBe(treeSitterClient);
    expect(code.filetype).toBe('rust');
    expect(code.streaming).toBe(true);
  });

  it('leaves prose coalesced instead of one renderable per paragraph', async () => {
    const theme = resolveTheme('dark');
    const content = Array.from({length: 100}, (_, index) => `Paragraph ${index + 1}.`).join('\n\n');
    const {markdown} = await renderMarkdown(content, theme);
    const {markdown: plain} = await renderMarkdown(content, theme, 'none');
    // An override the renderer cannot rule out for ordinary tokens is what
    // costs the coalescing, whatever that override ends up returning.
    const {markdown: unscoped} = await renderMarkdown(content, theme, (_token, context) =>
      context.defaultRender(),
    );

    expect(plain.getChildren()).toHaveLength(1);
    expect(markdown.getChildren()).toHaveLength(plain.getChildren().length);
    expect(unscoped.getChildren()).toHaveLength(100);
  });

  it('leaves every other block to the default renderer', async () => {
    const theme = resolveTheme('dark');
    const content = '# Heading\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n> quote\n\n- item\n';
    const {markdown} = await renderMarkdown(content, theme);
    const {markdown: plain} = await renderMarkdown(content, theme, 'none');

    const surfaces = (block: MarkdownRenderable): unknown[] =>
      block.getChildren().map(child => ({
        kind: child.constructor.name,
        ...(child instanceof CodeRenderable
          ? {fg: rgbToHex(child.fg), bg: rgbToHex(child.bg)}
          : {}),
      }));
    expect(surfaces(markdown)).toEqual(surfaces(plain));
    expect(markdown.getChildren().some(child => child instanceof TextTableRenderable)).toBe(true);
  });
});
