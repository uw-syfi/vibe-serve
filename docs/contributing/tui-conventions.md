# TUI conventions

What a key does in the VibeSys TUI is a citation, not a preference. This page
records the sources so a binding argument resolves against prior art rather than
taste, and so a reviewer can check a change against something.

There is no single TUI standard. There are four bodies of prior art that between
them settle most questions.

## Sources

**IBM Common User Access.** Introduced in 1987 under Systems Application
Architecture and documented in [Panel Design and User
Interaction](https://www.edm2.com/index.php/Common_User_Access) (1987) and the
[Advanced Interface Design Guide](http://www.susandoreydesigns.com/software/CommonUserAccessGUIDesign.pdf)
(1990). CUA 91 was adopted by Windows 95, and GNOME and KDE still track it. It is
the closest thing to a formal standard for keyboard-driven interfaces, and it is
where Tab-between-fields, F1-for-help, and Esc-cancels come from.

**WCAG 2.2.** Written for the web, but four criteria describe properties a
terminal can hold and fail:

| Criterion | Requirement | Why it applies here |
| --- | --- | --- |
| [1.4.1 Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color) | Colour is never the only carrier of information | Two of the eight themes are high-contrast and have almost no palette, so a colour-only signal says nothing in them |
| [2.4.7 Focus Visible](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible) | Keyboard focus has a visible indicator | The operator has to know where a keystroke lands before pressing it |
| [2.4.3 Focus Order](https://www.w3.org/WAI/WCAG22/Understanding/focus-order) | Focus order is meaningful and predictable | Panes are a hierarchy, and movement has to match it |
| [2.1.1 Keyboard](https://www.w3.org/WAI/WCAG22/Understanding/keyboard) | Everything is reachable by keyboard | A TUI has no other input |

**De facto terminal conventions**, unwritten but near-universal across `less`,
`vi`, `man`, `htop`, `tig`, `lazygit`, and `k9s`.

**readline line editing.** `Ctrl+A`, `Ctrl+E`, `Ctrl+K`, `Ctrl+W`, `Ctrl+U` in
any text input. This is what every shell does, so it is what fingers expect.

## Rules

### Movement

**Arrows move within the focused pane. Tab moves between panes.** This is CUA,
and it is the rule most often broken by accident. A binding that makes an arrow
key change which pane is focused is wrong, however convenient it seems in one
view, because it makes the same key mean two levels of movement depending on
where you are.

`Esc` moves out one level: cancel a modal, leave a pane, return to pane focus.
It never quits the application.

### Naming

| Key | Meaning | Source |
| --- | --- | --- |
| `/` | Search within the focused content | `less`, `vi`, `man`, `htop`, `tig`, `k9s` |
| `:` | Command | `vi` |
| `?` | Help | `less`, `htop`, `tig`, `lazygit` |
| `q` | Quit a read-only view | `less`, `man`, `htop` |
| `Esc` | Back one level | CUA |
| `Tab` / `Shift+Tab` | Next / previous pane | CUA |
| `F1` | Help | CUA |

`/` for search is about as strong as unwritten consensus gets. Where a command
prefix and a search prefix compete for `/`, search wins and commands move to `:`.

### Focus must not be colour alone

WCAG 1.4.1. A focused pane differs from an unfocused one by at least one channel
that is not colour. The TUI uses two: a marker in a reserved title gutter, and a
heavier frame. Colour reinforces them and is never the only signal.

The gutter is reserved rather than inserted, so the marker swaps a glyph instead
of pushing the title sideways. A title that moves on focus reads as the layout
shifting rather than the pane lighting up.

`focus.test.ts` holds this for every built-in theme, so it is a property rather
than an intention.

### Nothing moves that does not have to

Reserve space for anything that appears conditionally: focus markers, status
lines, counts, and variable-width numbers. A row that appears and disappears
resizes everything under it, and a list that jumps under the cursor costs more
than the row saved.

### Bindings are visible

The active bindings are shown at the bottom of the screen. A binding a person
has to already know is a binding they do not have.

## Applying this

A PR that adds or moves a binding names the rule it follows. If no rule covers
it, say so and propose one here in the same change, so the next person inherits
a decision rather than a precedent.
