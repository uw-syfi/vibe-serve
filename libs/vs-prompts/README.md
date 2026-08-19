# vs-prompts

Safe-by-default Jinja2 template rendering: strict-undefined rendering plus
static contract checks for sibling template families.

This is an internal import package shipped by the `vibesys` distribution. It
is not published as a separate Python distribution.

`vs-prompts` owns the two correctness properties a hand-rolled `Environment` +
string concatenation don't give you for free, without depending on VibeSys:

- `TemplateRenderer` renders `.j2` files and template strings with
  `jinja2.StrictUndefined` — a template referencing a variable no caller
  supplied raises `UndefinedError` at render time instead of silently
  producing an empty string. `{% if x is defined %}` guards keep working,
  since Jinja's `defined` test never evaluates the underlying value.
- `TemplateContract` catches the opposite direction: a variable a caller
  *does* supply but a template silently never references, so its content
  never reaches the rendered output. No undefined-variable check can see
  this direction; `TemplateContract` statically resolves each template's
  free variables (recursing through static `{% include %}`s, which share
  the parent scope) and flags any required variable a template omits
  without an explicit `{# vs-prompts:unused: <var> #}` skip marker.
- `FragmentFamily` generalizes "every variant of a discriminator must define
  every required small fragment, an empty file is a deliberate skip" to any
  per-key fragment set — the same contract `ComputeBackend` fragments need,
  without hardcoding that enum.

Applications own template content, directory layout, and what context they
pass; this package owns making a wrong pairing between the two fail loudly
instead of silently.
