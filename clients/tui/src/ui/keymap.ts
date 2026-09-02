import type { KeyEvent } from "@opentui/core";

export interface KeyChord {
  readonly name: string;
  readonly ctrl?: boolean;
  readonly shift?: boolean;
}

export interface TuiKeymap {
  readonly paneNext: readonly KeyChord[];
  readonly panePrevious: readonly KeyChord[];
}

/**
 * Application defaults live separately from key dispatch so changing a chord
 * does not require changing the behavior it invokes.
 *
 * Tab follows sequential focus order and is the portable fallback. Modified
 * arrows are a spatial alias where the terminal and operating system deliver
 * them to the application.
 */
export const DEFAULT_KEYMAP: TuiKeymap = {
  paneNext: [{ name: "tab" }, { name: "right", ctrl: true }],
  panePrevious: [
    { name: "tab", shift: true },
    { name: "left", ctrl: true },
  ],
};

export function matchesKeyChord(key: KeyEvent, chord: KeyChord): boolean {
  return (
    key.name === chord.name &&
    Boolean(key.ctrl) === Boolean(chord.ctrl) &&
    Boolean(key.shift) === Boolean(chord.shift) &&
    !key.meta &&
    !key.option &&
    !key.super &&
    !key.hyper
  );
}

export function matchesKeymapAction(
  key: KeyEvent,
  chords: readonly KeyChord[],
): boolean {
  return chords.some((chord) => matchesKeyChord(key, chord));
}

export function keyChordLabel(chord: KeyChord): string {
  const modifiers = [
    chord.ctrl ? "Ctrl" : null,
    chord.shift ? "Shift" : null,
  ].filter((value): value is string => value !== null);
  const name = chord.name === "tab" ? "Tab" : titleCase(chord.name);
  return [...modifiers, name].join("+");
}

function titleCase(value: string): string {
  return value.length === 0
    ? value
    : `${value[0]?.toUpperCase()}${value.slice(1)}`;
}
