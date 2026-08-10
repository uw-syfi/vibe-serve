import {describe, expect, it} from 'bun:test';
import {
  CONVERSATION_ROLES,
  contrastRatio,
  DEFAULT_THEME_NAME,
  ensureContrast,
  isThemeName,
  listThemes,
  mix,
  relativeLuminance,
  resolveTheme,
  THEME_NAMES,
  type Theme,
} from './theme.js';

describe('theme selection', () => {
  it('ships the four light/dark pairs with dark as the baseline', () => {
    expect(THEME_NAMES).toEqual([
      'dark',
      'light',
      'solarized-dark',
      'solarized-light',
      'catppuccin-mocha',
      'catppuccin-latte',
      'high-contrast-dark',
      'high-contrast-light',
    ]);
    expect(DEFAULT_THEME_NAME).toBe('dark');
    const byAppearance = listThemes().reduce<Record<string, number>>((counts, theme) => {
      counts[theme.appearance] = (counts[theme.appearance] ?? 0) + 1;
      return counts;
    }, {});
    expect(byAppearance).toEqual({light: 4, dark: 4});
  });

  it('resolves known names and falls back to the default for anything else', () => {
    expect(resolveTheme('solarized-light').name).toBe('solarized-light');
    expect(resolveTheme('monokai').name).toBe(DEFAULT_THEME_NAME);
    expect(resolveTheme(undefined).name).toBe(DEFAULT_THEME_NAME);
    expect(resolveTheme(null).name).toBe(DEFAULT_THEME_NAME);
    expect(resolveTheme('').name).toBe(DEFAULT_THEME_NAME);
  });

  it('narrows theme names without trusting arbitrary strings', () => {
    expect(isThemeName('catppuccin-mocha')).toBe(true);
    expect(isThemeName('Dark')).toBe(false);
    expect(isThemeName(undefined)).toBe(false);
  });

  it('documents the four shades that semantic consolidation merged', () => {
    const dark = resolveTheme('dark');
    expect(dark.success).toBe('#22c55e');
    expect(dark.info).toBe('#38bdf8');
    expect(dark.textPrimary).toBe('#e2e8f0');
    expect(dark.conversation.analysis.label).toBe('#a78bfa');
  });

  it('keeps the dark baseline pinned to the pre-theme appearance', () => {
    const dark = resolveTheme('dark');
    expect(dark.canvas).toBe('#0f172a');
    expect(dark.accent).toBe('#22d3ee');
    expect(dark.border).toBe('#475569');
    expect(dark.conversation.assistant).toEqual({
      border: '#0891b2',
      background: '#0f1b24',
      label: '#67e8f9',
      content: '#e2e8f0',
    });
    expect(dark.conversation.failure.label).toBe('#f87171');
    expect(dark.toolCall).toEqual({foreground: '#dbeafe', background: '#1e3a8a'});
    expect(dark.markdown.code).toBe('#a5f3fc');
    expect(dark.markdown.codeBackground).toBe('#1e293b');
  });
});

describe('semantic roles', () => {
  const themes = listThemes();

  it.each(
    themes.map(theme => [theme.name, theme] as const),
  )('%s defines every conversation role and Markdown color', (_name, theme: Theme) => {
    for (const role of CONVERSATION_ROLES) {
      const colors = theme.conversation[role];
      for (const value of Object.values(colors)) {
        expect(value).toMatch(/^#[0-9a-f]{6}$/i);
      }
    }
    for (const value of Object.values(theme.markdown)) {
      expect(value).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it.each(
    themes.map(theme => [theme.name, theme] as const),
  )('%s keeps body text readable on the canvas and its surfaces', (_name, theme: Theme) => {
    const minimum = theme.name.startsWith('high-contrast') ? 7 : 4.5;
    expect(contrastRatio(theme.textPrimary, theme.canvas)).toBeGreaterThanOrEqual(minimum);
    expect(contrastRatio(theme.textStrong, theme.canvas)).toBeGreaterThanOrEqual(minimum);
    expect(contrastRatio(theme.markdown.default, theme.canvas)).toBeGreaterThanOrEqual(minimum);
    expect(contrastRatio(theme.textMuted, theme.canvas)).toBeGreaterThanOrEqual(3);
    expect(contrastRatio(theme.textSubtle, theme.canvas)).toBeGreaterThanOrEqual(3);
  });

  it.each(
    themes.map(theme => [theme.name, theme] as const),
  )('%s keeps every conversation card label and body readable on its own fill', (_name, theme: Theme) => {
    const minimum = theme.name.startsWith('high-contrast') ? 7 : 4.5;
    for (const role of CONVERSATION_ROLES) {
      const {label, content, background} = theme.conversation[role];
      expect(contrastRatio(label, background)).toBeGreaterThanOrEqual(minimum);
      expect(contrastRatio(content, background)).toBeGreaterThanOrEqual(minimum);
    }
    expect(
      contrastRatio(theme.toolCall.foreground, theme.toolCall.background),
    ).toBeGreaterThanOrEqual(minimum);
    expect(
      contrastRatio(theme.toolResult.foreground, theme.toolResult.background),
    ).toBeGreaterThanOrEqual(minimum);
  });

  it.each(
    themes.map(theme => [theme.name, theme] as const),
  )('%s keeps status colors distinguishable from each other and the canvas', (_name, theme: Theme) => {
    for (const status of [theme.success, theme.warning, theme.error] as const) {
      expect(contrastRatio(status, theme.canvas)).toBeGreaterThanOrEqual(3);
    }
    expect(theme.success).not.toBe(theme.warning);
    expect(theme.warning).not.toBe(theme.error);
    expect(theme.success).not.toBe(theme.error);
  });

  it.each(
    themes.map(theme => [theme.name, theme] as const),
  )('%s keeps panel borders visible against the canvas', (_name, theme: Theme) => {
    for (const border of [theme.border, theme.borderStrong, theme.borderFocus] as const) {
      expect(contrastRatio(border, theme.canvas)).toBeGreaterThanOrEqual(1.7);
    }
  });

  it('orients light and dark themes in opposite luminance directions', () => {
    for (const theme of themes) {
      const canvas = relativeLuminance(theme.canvas);
      const text = relativeLuminance(theme.textPrimary);
      if (theme.appearance === 'light') {
        expect(canvas).toBeGreaterThan(text);
        expect(canvas).toBeGreaterThan(0.5);
      } else {
        expect(canvas).toBeLessThan(text);
        expect(canvas).toBeLessThan(0.5);
      }
    }
  });

  it('gives light and dark members of a pair genuinely different palettes', () => {
    const pairs: Array<[string, string]> = [
      ['dark', 'light'],
      ['solarized-dark', 'solarized-light'],
      ['catppuccin-mocha', 'catppuccin-latte'],
      ['high-contrast-dark', 'high-contrast-light'],
    ];
    for (const [darkName, lightName] of pairs) {
      const dark = resolveTheme(darkName);
      const light = resolveTheme(lightName);
      expect(dark.canvas).not.toBe(light.canvas);
      expect(dark.textPrimary).not.toBe(light.textPrimary);
      expect(dark.conversation.assistant.background).not.toBe(
        light.conversation.assistant.background,
      );
    }
  });
});

describe('color math', () => {
  it('computes WCAG contrast ratios at the known extremes', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(contrastRatio('#ffffff', '#ffffff')).toBeCloseTo(1, 5);
    expect(contrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 5);
  });

  it('blends colors linearly and clamps the blend factor', () => {
    expect(mix('#000000', '#ffffff', 0.5)).toBe('#808080');
    expect(mix('#000000', '#ffffff', 0)).toBe('#000000');
    expect(mix('#000000', '#ffffff', 2)).toBe('#ffffff');
    expect(mix('#000000', '#ffffff', -1)).toBe('#000000');
  });

  it('accepts shorthand hex', () => {
    expect(relativeLuminance('#fff')).toBeCloseTo(relativeLuminance('#ffffff'), 10);
  });

  it('returns the original color when it already clears the target ratio', () => {
    expect(ensureContrast('#ffffff', '#000000', 4.5)).toBe('#ffffff');
  });

  it('lifts a low-contrast color until it clears the target ratio', () => {
    const lifted = ensureContrast('#333333', '#000000', 4.5);
    expect(contrastRatio(lifted, '#000000')).toBeGreaterThanOrEqual(4.5);
    const darkened = ensureContrast('#cccccc', '#ffffff', 4.5);
    expect(contrastRatio(darkened, '#ffffff')).toBeGreaterThanOrEqual(4.5);
  });
});
