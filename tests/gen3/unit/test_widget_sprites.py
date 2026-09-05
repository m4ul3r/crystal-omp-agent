"""Every species the widget can be asked to draw has a sprite to draw.

The party list shows a sprite beside each member, keyed on the species name the
ROM reports. That name is spelled for a Game Boy screen -- "MR. MIME",
"FARFETCH'D", "NIDORAN<female>" -- while the decompilation names its sprite
directories in lowercase alphanumerics, so a slug rule sits between them.

The rule is written TWICE: in Python in `widget/make_sprites.py`, which emits
the files, and in JavaScript in `Model.js:spriteSlug`, which asks for them at
runtime. Two implementations of one rule is exactly the arrangement that drifts
silently, and the failure is invisible -- `Image` with a bad source renders
nothing and the row quietly falls back to text. Nobody notices until they look
at a screenshot.

So these tests transliterate the JavaScript and check it against both the
Python and the files on disk. Visual confirmation happened once, by rendering
the party rows offscreen and looking at the result (`docs/party-sprites.png`);
this is the part that can run every time.
"""

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
SPRITES = ROOT / "widget" / "poke.run" / "sprites"
MODEL_JS = ROOT / "widget" / "poke.run" / "Model.js"


def js_slug(species: str) -> str:
    """`Model.js:spriteSlug`, transliterated.

    Kept deliberately literal -- same order, same special case -- so a reader
    can diff it against the JavaScript by eye. A clever Python rewrite would
    defeat the purpose of the comparison.
    """
    if not isinstance(species, str) or species == "":
        return ""
    raw = species.lower()
    if "nidoran" in raw:
        return "nidoran_f" if "\u2640" in species else "nidoran_m"
    return "".join(c for c in raw if c.isascii() and c.isalnum())


@pytest.fixture(scope="module")
def sprite_files():
    if not SPRITES.is_dir():
        pytest.skip("sprites not generated; run widget/make_sprites.py")
    return {p.stem for p in SPRITES.glob("*.png")}


def test_the_generator_and_the_widget_agree_on_the_slug():
    """One rule, two languages. This is the pair that drifts."""
    from widget.make_sprites import slug_for

    for name in ("BULBASAUR", "MR. MIME", "FARFETCH'D", "HO-OH", "PORYGON2",
                 "NIDORAN\u2640", "NIDORAN\u2642", "MIME JR."):
        assert js_slug(name) == slug_for(name), name


def test_gendered_nidoran_do_not_collide():
    """Stripping the symbol would map both to `nidoran`, and one of the two
    would silently render as the other -- a wrong sprite is worse than none."""
    female, male = js_slug("NIDORAN\u2640"), js_slug("NIDORAN\u2642")
    assert female != male
    assert {female, male} == {"nidoran_f", "nidoran_m"}


def test_an_egg_asks_for_nothing():
    """`spriteFor` short-circuits on eggs. An egg has no species to draw, and a
    missing file is a broken image where a blank is correct."""
    assert js_slug("") == ""
    assert js_slug(None) == ""


SPECIES_NAMES = ROOT / "pret" / "src" / "data" / "text" / "species_names_en.h"


def rom_species_names() -> dict:
    """`SPECIES_FOO -> "FOO"` from the ROM's own name table.

    This is the exact set of strings the widget can ever be handed, which is
    why it is the coverage bar. A hand-maintained list would drift, and the
    Hoenn dex JSON is the wrong set entirely -- the party can hold a traded
    Kanto species that no regional dex mentions.
    """
    if not SPECIES_NAMES.exists():
        pytest.skip("pokeruby decomp not checked out")
    pattern = re.compile(r'\[SPECIES_([A-Z0-9_]+)\]\s*=\s*_\("([^"]*)"\)')
    return {m.group(1): m.group(2)
            for m in pattern.finditer(SPECIES_NAMES.read_text())}


def test_every_drawable_species_has_a_sprite_under_the_name_asked_for(sprite_files):
    """Generator and consumer, checked against the real name set.

    This is the test that would have caught the bug it was written after:
    `make_sprites.py` named its output after pret's DIRECTORY (`mr_mime.png`,
    `ho_oh.png`) while the widget derives its request from the ROM's DISPLAY
    name and asks for `mrmime.png`. Both sides looked correct in isolation and
    Ho-Oh and Mr. Mime silently rendered nothing.

    Scoped to species pret can actually draw: a species whose art the generator
    skips (Unown and Castform keep their forms in per-form directories) has no
    file by design, and that is a different claim from a broken name.
    """
    from widget.make_sprites import SKIP, SOURCE

    missing = []
    for const, display in rom_species_names().items():
        if display in ("??????????", "-") or const.startswith("OLD_UNOWN"):
            continue
        directory = const.lower()
        if directory in SKIP or not (SOURCE / directory / "front.png").exists():
            continue
        slug = js_slug(display)
        if slug not in sprite_files:
            missing.append(f"{display} (pret/{directory}) -> {slug}.png")
    assert not missing, f"{len(missing)} species cannot find their sprite: {missing[:6]}"


def test_the_punctuated_species_are_reachable(sprite_files):
    """The three shapes that break a naive slug, pinned by name.

    Ho-Oh has a hyphen, Mr. Mime a full stop and a space, Farfetch'd an
    apostrophe. Each is spelled with an underscore by pret and without one by
    the widget, so they are the canaries for the whole rule.
    """
    for display in ("HO-OH", "MR. MIME", "FARFETCH'D"):
        assert js_slug(display) in sprite_files, f"{display} has no sprite"


def test_the_generator_emits_what_the_widget_requests():
    """`asked_for` mirrors `spriteSlug`, including the Nidoran exception."""
    from widget.make_sprites import asked_for

    assert asked_for("mr_mime") == "mrmime"
    assert asked_for("ho_oh") == "hooh"
    assert asked_for("bulbasaur") == "bulbasaur"
    # Underscores survive here and only here: the widget special-cases the
    # gendered pair because stripping the symbol would collide them.
    assert asked_for("nidoran_f") == "nidoran_f"
    assert asked_for("nidoran_m") == "nidoran_m"


def test_the_javascript_rule_still_looks_like_the_one_transliterated_here():
    """A guard on the guard.

    If someone rewrites `spriteSlug` -- adds a form suffix, changes the Nidoran
    case -- the transliteration above goes stale and these tests start proving
    nothing while still passing. Pinning the two load-bearing lines is cheap
    insurance against a green suite that has stopped looking.
    """
    source = MODEL_JS.read_text()
    assert "function spriteSlug(species)" in source
    assert 'return species.indexOf("\\u2640") !== -1 ? "nidoran_f" : "nidoran_m"' in source
    assert re.search(r'return\s+slug === ""\s*\?\s*""\s*:\s*"sprites/"\s*\+\s*slug\s*\+\s*"\.png"',
                     source), "spriteFor no longer builds sprites/<slug>.png"
