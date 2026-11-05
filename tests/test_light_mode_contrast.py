"""Light mode must repaint interaction states, not just resting ones.

Tailwind's palette is compiled into the CDN build, so every utility carries
the dark theme's hex wherever it appears and light mode works by overriding
those utilities with CSS variables. The overrides named only base classes —
`.bg-paper`, `.text-ink` — and Tailwind compiles each variant into its own
separate class, so `.hover\\:bg-paper` was never covered. Hovering a row in
"What's on air by the hour" therefore painted it #111827 under near-black
text, and hover:text-ink and hover:border-ink both resolved to #F4F6FA, which
is white on white. It ran through every tab, because those utilities are how
the dashboard says "this is interactive".

Nothing caught it because nothing was looking, so this looks: every state
variant used anywhere in the UI must have a light-mode repaint.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "static" / "index.html"

# variants that change a colour and therefore need a light-mode value
VARIANT_RE = re.compile(r'\b(hover|group-hover|focus|active):(bg|text|border)-([a-z0-9]+)')

# Colour tokens that are identical in both themes need no repaint: white and
# the semantic hues are already pitched to work on either surface.
THEME_NEUTRAL = {"white", "blue", "blue6", "red6", "amber6", "green6", "transparent"}


def _sources():
    yield INDEX
    yield from sorted((ROOT / "static" / "js").glob("*.js"))


def _variants_in_use():
    used = set()
    for path in _sources():
        for variant, prop, token in VARIANT_RE.findall(path.read_text(encoding="utf-8")):
            if token not in THEME_NEUTRAL:
                used.add((variant, prop, token))
    return used


def test_every_interaction_colour_has_a_light_mode_repaint():
    css = INDEX.read_text(encoding="utf-8")
    light = css[css.find(':root[data-theme="light"]'):]
    missing = []
    for variant, prop, token in sorted(_variants_in_use()):
        # the escaped class as it appears in a stylesheet, e.g. .hover\:bg-paper
        needle = rf"\.{variant}\\:{prop}-{token}\b"
        if not re.search(needle, light):
            missing.append(f"{variant}:{prop}-{token}")
    assert not missing, (
        "these interaction colours keep the dark palette's hex in light mode, "
        "which is how the on-air rows turned black under black text: "
        + ", ".join(missing))


def test_the_primary_button_hover_is_a_token_not_a_fixed_colour():
    """The pill is bone with dark text in one theme and dark with white text
    in the other, so a single hardcoded hover colour has to collide with one
    of them. It used to be #fff, which in light mode put white on white."""
    css = INDEX.read_text(encoding="utf-8")
    rule = re.search(r"button\.bg-navy:hover[^{]*\{([^}]*)\}", css)
    assert rule, "the primary button's hover rule has gone missing"
    assert "--btn-hi" in rule.group(1), (
        "primary button hover is back to a fixed colour; it must use the "
        f"theme token, got: {rule.group(1).strip()}")
    # and both themes must define it
    assert re.search(r":root\s*\{[^}]*--btn-hi", css, re.S), "dark mode lacks --btn-hi"
    assert re.search(r':root\[data-theme="light"\]\s*\{[^}]*--btn-hi', css, re.S), \
        "light mode lacks --btn-hi"


def test_controls_are_excluded_from_the_surface_repaint():
    """bg-navy buttons carry hover:bg-navy2 too. Repainting that utility
    without excluding them outranked the button's own rule on specificity and
    painted every primary button near-white while its label stayed white."""
    css = INDEX.read_text(encoding="utf-8")
    rule = re.search(r'\.hover\\:bg-navy2:hover(:not\([^)]*\))?\s*\{', css)
    assert rule and rule.group(1), (
        "the hover:bg-navy2 repaint must exclude .bg-navy, or it repaints the "
        "primary buttons it is not meant to touch")
    assert "bg-navy" in rule.group(1)


def test_the_rails_keep_their_dark_hovers():
    """The sidebar and N-Pro's rail stay dark in both themes. N-Pro renders
    its recent-stories list into the rail from JS, so a canvas-wide repaint
    would drop a pale strip inside a near-black column."""
    css = INDEX.read_text(encoding="utf-8")
    light = css[css.find(':root[data-theme="light"]'):]
    assert re.search(r'#sidebar \.hover\\:bg-navy2:hover', light), \
        "the sidebar's own hover is no longer held to the rail's dark ladder"
    assert "--rail-hi" in light


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except Exception as exc:
                failures += 1
                print(f"  FAIL {name}: {exc}")
    raise SystemExit(failures)
