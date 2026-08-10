import {describe, expect, it} from 'bun:test';
import {parseInput, slashCommandRange, suggestSlashCommands} from './commands.js';

describe('parseInput', () => {
  it('accepts the intentionally small slash-command surface', () => {
    expect(parseInput('/history').request?.type).toBe('query.history');
    expect(parseInput('/perf')).toMatchObject({
      request: {type: 'query.performance'},
      responseView: 'perf',
    });
    expect(parseInput('/chat what changed in the latest round?')).toEqual({
      localView: 'chat',
      chatMessage: 'what changed in the latest round?',
    });
  });

  it('parses run-control commands', () => {
    expect(parseInput('/pause')).toEqual({request: {type: 'command.pause'}});
    expect(parseInput('/resume')).toEqual({request: {type: 'command.resume'}});
    expect(parseInput('/steer prioritize the KV cache path')).toEqual({
      request: {type: 'command.steer', text: 'prioritize the KV cache path'},
    });
  });

  it('requires a message for /steer', () => {
    expect(parseInput('/steer').error).toContain('Usage: /steer');
    expect(parseInput('/steer   ').error).toContain('Usage: /steer');
  });

  it('sends ordinary text to the supervision chat endpoint', () => {
    expect(parseInput('what is happening?')).toEqual({
      request: {type: 'query.chat', text: 'what is happening?'},
    });
    expect(parseInput('')).toEqual({error: 'Enter a question or use /help.'});
  });

  it('keeps inspection commands out of the public command surface', () => {
    expect(parseInput('/round 4').error).toContain('Unknown command');
    expect(parseInput('/invocation abc').error).toContain('Unknown command');
    expect(parseInput('/show workspace/file').error).toContain('Unknown command');
  });

  it('provides local help without a backend request', () => {
    expect(parseInput('/help')).toEqual({localView: 'help'});
  });

  it('opens chat without requiring an initial question', () => {
    expect(parseInput('/chat')).toEqual({localView: 'chat'});
    expect(parseInput('/chat   ')).toEqual({localView: 'chat'});
  });

  it('lists themes bare and selects a known theme by name', () => {
    expect(parseInput('/theme')).toEqual({localView: 'theme'});
    expect(parseInput('/theme   ')).toEqual({localView: 'theme'});
    expect(parseInput('/theme solarized-light')).toEqual({
      localView: 'theme',
      themeName: 'solarized-light',
    });
  });

  it('rejects an unknown theme name with the available list', () => {
    const parsed = parseInput('/theme monokai');
    expect(parsed.error).toContain('Unknown theme: monokai');
    expect(parsed.error).toContain('catppuccin-mocha');
    expect(parsed.localView).toBeUndefined();
  });
});

describe('slash-command input helpers', () => {
  it('suggests available commands from a slash prefix', () => {
    expect(suggestSlashCommands('/').map(command => command.name)).toEqual([
      '/help',
      '/chat',
      '/pause',
      '/resume',
      '/steer',
      '/history',
      '/perf',
      '/theme',
    ]);
    expect(suggestSlashCommands('/hi').map(command => command.name)).toEqual(['/history']);
    expect(suggestSlashCommands('/history ')).toEqual([]);
    expect(suggestSlashCommands('history')).toEqual([]);
  });

  it('finds a leading slash-command token for syntax highlighting', () => {
    expect(slashCommandRange('/history')).toEqual({start: 0, end: 8});
    expect(slashCommandRange('/steer inspect the cache')).toEqual({start: 0, end: 6});
    expect(slashCommandRange('/')).toBeNull();
    expect(slashCommandRange('show /history')).toBeNull();
  });
});
