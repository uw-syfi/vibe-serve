# TUI conventions

What a key does in the VibeSys TUI should be a citation, not a preference. This
page records the sources so a binding argument resolves against prior art rather
than taste, and so a reviewer can check a change against something.

Read it in two parts. [Rules in force](#rules-in-force) describe what the code
does today, so a change that breaks one is a regression. [Proposed: movement and
naming](#proposed-movement-and-naming) is design that is not implemented, kept
here so the argument is settled once rather than reopened per PR.

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

## Rules in force

### Focus is never carried by colour alone

WCAG 1.4.1. A focused pane differs from a resting one by at least one channel
that is not colour. That channel is the marker: `paneTitle` reserves a gutter
cell in every pane title and swaps `▸` into it on focus. Colour reinforces it,
`borderFocus` against `border`, and is never the only signal.

Border weight is not one of the channels. `paneBorderStyle` returns `rounded`
whether or not a pane holds focus. A heavier frame was tried and dropped: the
only heavier box-drawing weights are square (`┏┓┗┛`), because Unicode has no
heavy rounded corner, so the swap trades rounded corners for square ones. That
is a change of shape rather than of weight, and reads as the pane becoming a
different kind of object rather than a louder one.

The gutter is reserved rather than inserted, so the marker swaps a glyph instead
of pushing the title sideways. A title that moves on focus reads as the layout
shifting rather than the pane lighting up.

`focus.test.ts` holds this for every built-in theme, so it is a property rather
than an intention. It also pins the direction of the colour channel: a focused
border is never lower-contrast against the canvas than a resting one.

### One surface wears the focus treatment, and it is a pane

`focusedPane` is the single authority for which one. Every pane compares its own
`PaneId` against it, so at most one can answer yes, and a surface that is not a
pane never asks. Two cases follow from that and are worth naming, because both
were shipped wrong once:

- A box nested inside a pane does not repeat the treatment. The chat's `Message`
  composer sits inside the chat pane, so the pane frame carries the marker and
  the composer keeps the resting frame. Which box holds the cursor is said by
  the cursor and by the hint line under it.
- A surface that takes the keys is a pane, whatever its shape. The expanded todo
  list is a strip rather than a column, but `keybindings.ts` routes the arrow
  keys to it, so it is a `PaneId` and the pane it opened over goes back to rest.
  The same holds for a presentation swap: below `MIN_SPLIT_WIDTH` a
  visualization is drawn through the overlay, and that overlay is then the
  performance pane, with its title and its marker.

Zoom is a separate question from focus. `visiblePaneIds` is the set the content
row can be given to, and the todo list is deliberately not in it: it is as tall
as its own contents, so `F4` over it leaves the layout alone.

### Esc goes back one level

`Esc` cancels a modal, closes an overlay, leaves a drill-down, or returns pane
focus to the left column. It never quits the application. This is CUA, and it is
the one movement rule the current bindings already keep.

### Nothing moves that does not have to

Reserve space for anything that appears conditionally: focus markers, status
lines, counts, and variable-width numbers. A row that appears and disappears
resizes everything under it, and a list that jumps under the cursor costs more
than the row saved.

### Bindings are visible

The active bindings are shown on the key-help line at the bottom of the screen,
just above the command input, and that line changes with whichever surface is in
front. A binding a person has to already know is a binding they do not have.

## Proposed: movement and naming

Nothing in this section is implemented. It is where the movement and naming
arguments landed, recorded so they are not reopened per PR. A PR that adopts one
of these lines moves it up into [Rules in force](#rules-in-force) in the same
change.

### Movement

Today pane focus is `Ctrl+W`, which cycles the chat, the left column, and the
visualization pane. Inside the left column, `←` and `→` move between the agent
strip and the transcript, `↑` and `↓` move within whichever of the two holds
focus, and `Tab` / `Shift+Tab` step through agents once the command input's
completion has declined the key.

CUA says the opposite: **arrows move within the focused pane, and Tab moves
between panes**. A binding that makes an arrow key change which pane is focused
makes the same key mean two levels of movement depending on where you are, which
is what `←` and `→` do now. That is the asymmetry this rule would fix.

The replacement keymap exists on the unmerged `adi/tui-keymap-defaults` branch,
which has no PR: `DEFAULT_KEYMAP` binds `paneNext` to `Tab` and `Ctrl+Right`,
and `panePrevious` to `Shift+Tab` and `Ctrl+Left`. Until that lands, `Ctrl+W` is
the binding and the key-help line says so.

### Naming

Proposed prefixes and single keys, none of them bound today. The TUI's only
prefix is `/`, and it means command (`/help`, `/theme`, `/open-round`), not
search. There is no search and no `:` prefix. `F2`, `F3` and `F4` toggle todos,
the latest prompt, and pane zoom.

| Key | Proposed meaning | Source | Bound to today |
| --- | --- | --- | --- |
| `/` | Search within the focused content | `less`, `vi`, `man`, `htop`, `tig`, `k9s` | Command prefix |
| `:` | Command | `vi` | Nothing |
| `?` | Help | `less`, `htop`, `tig`, `lazygit` | Nothing (`/help` instead) |
| `q` | Quit a read-only view | `less`, `man`, `htop` | Nothing |
| `Tab` / `Shift+Tab` | Next / previous pane | CUA | Next / previous agent |
| `F1` | Help | CUA | Nothing |

`/` for search is about as strong as unwritten consensus gets, which is the
argument for moving commands to `:`. It is a proposal rather than a rule because
it renames every typed command, so it needs its own change and its own
deprecation.

## Applying this

A PR that adds or moves a binding names the rule it follows. If no rule covers
it, say so and propose one here in the same change, so the next person inherits
a decision rather than a precedent.
