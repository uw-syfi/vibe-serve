import { describe, expect, it } from "bun:test";
import type { KeyEvent } from "@opentui/core";
import {
  DEFAULT_KEYMAP,
  keyChordLabel,
  matchesKeymapAction,
} from "./keymap.js";

function key(name: string, modifiers: Partial<KeyEvent> = {}): KeyEvent {
  return { name, ...modifiers } as KeyEvent;
}

describe("default TUI keymap", () => {
  it("moves pane focus forward with Tab or Ctrl+Right", () => {
    expect(matchesKeymapAction(key("tab"), DEFAULT_KEYMAP.paneNext)).toBe(true);
    expect(
      matchesKeymapAction(
        key("right", { ctrl: true }),
        DEFAULT_KEYMAP.paneNext,
      ),
    ).toBe(true);
    expect(matchesKeymapAction(key("right"), DEFAULT_KEYMAP.paneNext)).toBe(
      false,
    );
  });

  it("moves pane focus backward with Shift+Tab or Ctrl+Left", () => {
    expect(
      matchesKeymapAction(
        key("tab", { shift: true }),
        DEFAULT_KEYMAP.panePrevious,
      ),
    ).toBe(true);
    expect(
      matchesKeymapAction(
        key("left", { ctrl: true }),
        DEFAULT_KEYMAP.panePrevious,
      ),
    ).toBe(true);
    expect(matchesKeymapAction(key("left"), DEFAULT_KEYMAP.panePrevious)).toBe(
      false,
    );
  });

  it("formats bindings for contextual help", () => {
    expect(keyChordLabel(DEFAULT_KEYMAP.paneNext[0]!)).toBe("Tab");
    expect(keyChordLabel(DEFAULT_KEYMAP.panePrevious[0]!)).toBe("Shift+Tab");
    expect(keyChordLabel(DEFAULT_KEYMAP.paneNext[1]!)).toBe("Ctrl+Right");
  });
});
