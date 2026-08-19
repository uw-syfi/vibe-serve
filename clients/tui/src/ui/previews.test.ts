import {describe, expect, it} from 'bun:test';
import {elapsedLabel, promptPreview, toolOutputPreview} from './previews.js';

describe('conversation previews', () => {
  it('limits tool output without discarding the underlying content', () => {
    const content = Array.from({length: 20}, (_, index) => `line ${index + 1}`).join('\n');
    const preview = toolOutputPreview(content);

    expect(preview).toContain('line 12');
    expect(preview).not.toContain('line 13');
    expect(preview).toContain('8 more lines hidden');
    expect(content).toContain('line 20');
  });

  it('collapses and expands long prompts', () => {
    const content = Array.from({length: 20}, (_, index) => `prompt line ${index + 1}`).join('\n');

    expect(promptPreview(content, false)).toMatchObject({hiddenLines: 8});
    expect(promptPreview(content, false).content).not.toContain('prompt line 13');
    expect(promptPreview(content, true).content).toContain('prompt line 20');
  });
});

describe('elapsed labels', () => {
  it('drops to the coarsest unit the duration reaches', () => {
    expect(elapsedLabel(45_000)).toBe('45s');
    expect(elapsedLabel(65_000)).toBe('1m 5s');
    expect(elapsedLabel(3_725_000)).toBe('1h 2m');
    expect(elapsedLabel(0)).toBe('0s');
    expect(elapsedLabel(-1)).toBe('0s');
  });
});
