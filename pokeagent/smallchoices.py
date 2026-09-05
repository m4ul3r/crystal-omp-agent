"""Where the local model is allowed to decide, and where it is not.

The user's ask was to push as many *little* decisions as possible onto the
local `gemma4:e4b` instead of a frontier model. This module is the boundary
that makes that safe, and the boundary is drawn from measurement rather than
taste.

Measured on the live server (`gemma4:e4b`, 8B, ~1.5 s per call):

* **5/5 correct** on clean single-hop type questions -- "which type is
  strongest against WATER", "which is immune to GROUND".
* **Wrong** on multi-hop inference: asked which of TACKLE / THUNDERBOLT /
  EMBER beats a Water type it answered TACKLE, because that needs
  move -> type -> matchup and it only reliably does one link.
* **Wrong** on judgment where the harness has exact numbers: asked whether to
  heal at 12% HP in a wild battle it said no, reasoning that "wild battles
  don't usually allow healing", which is simply false.

So the rule this module enforces:

    The harness computes everything it CAN compute exactly.
    The model decides only what has no computable answer,
    or breaks a tie the maths has already declared equal.

Crucially, when the model IS consulted the option list has already been
filtered by the maths, so a wrong pick is still a *valid* pick. That is what
makes delegating cheap: the downside of a bad answer is bounded by
construction, not by trusting the model.

Sanctioned (no computable answer, or a genuine tie):
  - nicknames
  - choosing among moves whose damage spans overlap within EPSILON
  - which of several equally-accessible dex targets to sweep next
  - which of several catch candidates to prefer when coverage gain ties
  - flavour: whether to talk to an optional NPC

Never (the harness has an exact answer):
  - move choice when the damage maths separates the options
  - type effectiveness, STAB, or whether a KO is certain
  - whether to heal (an HP threshold is a number, not an opinion)
  - anything about legality: PP, switch validity, escape odds
"""

import logging

log = logging.getLogger("pokeagent.smallchoices")

#: Two moves are "tied" when their expected damage differs by less than this
#: fraction of the defender's max HP. Below that the choice is genuinely
#: arbitrary and worth handing to the model for flavour.
EPSILON = 0.08


class SmallChoices:
    """The single sanctioned entry point for model-made decisions."""

    def __init__(self, brain=None):
        self.brain = brain
        self.consulted = 0
        self.declined = 0
        self.last_reason = None

    @property
    def enabled(self):
        return self.brain is not None and getattr(self.brain, "enabled", False)

    #: A move-learn prompt blocks a battle turn, so it gets a tighter leash
    #: than a nickname does. A dead host costs nothing either way -- the
    #: breaker short-circuits after three failures -- but a SLOW one would
    #: otherwise stall the fight for the full default.
    BATTLE_TIMEOUT = 8.0

    def _ask(self, kind, question, options, fallback, context=None,
             timeout=None):
        if not self.enabled:
            self.declined += 1
            self.last_reason = "no brain attached"
            return fallback
        if len(options) < 2:
            self.declined += 1
            self.last_reason = "only one option; nothing to decide"
            return fallback
        self.consulted += 1
        answer = self.brain.choose(question, options, fallback=fallback,
                                   context=context, timeout=timeout)
        self.last_reason = getattr(self.brain, "last_reason", None)
        log.info("%s: %s -> %s (%s)", kind, question, answer, self.last_reason)
        return answer

    # ---- sanctioned decisions ---------------------------------------------

    def nickname(self, species, fallback):
        """No right answer exists, so this is the ideal delegation."""
        if not self.enabled:
            return fallback
        self.consulted += 1
        name = self.brain.nickname(species, fallback=fallback)
        self.last_reason = getattr(self.brain, "last_reason", None)
        return name

    def tied_move(self, analysis, fallback_slot, defender_max_hp=None):
        """Break a tie between moves the damage maths cannot separate.

        `analysis` is `tactics.outlook()`. Only moves whose expected damage is
        within EPSILON of the best are offered, and only when there is no
        certain KO -- a guaranteed kill is arithmetic, not a preference.
        """
        moves = [m for m in analysis.get("moves", []) if m.get("power")]
        if not moves:
            return fallback_slot
        # A certain KO is never a tie: the maths decides.
        if any(m.get("hits_to_ko") == 1 and m.get("effective_accuracy", 0) >= 95
               for m in moves):
            self.declined += 1
            self.last_reason = "a certain KO is arithmetic, not a preference"
            return fallback_slot

        max_hp = defender_max_hp or max(
            (m.get("damage_max") or 1) for m in moves
        ) or 1
        best = max((m.get("damage_max") or 0) for m in moves)
        tied = [
            m for m in moves
            if best - (m.get("damage_max") or 0) <= EPSILON * max_hp
        ]
        if len(tied) < 2:
            self.declined += 1
            self.last_reason = "the damage maths separates the moves"
            return fallback_slot

        labels = {f"{m['name']} (slot {m['slot']})": m["slot"] for m in tied}
        picked = self._ask(
            "tied-move",
            "These moves do near-identical damage here. Which is the better "
            "play?",
            sorted(labels),
            fallback=next(k for k, v in labels.items() if v == fallback_slot)
            if fallback_slot in labels.values() else sorted(labels)[0],
            context=self._battle_context(analysis),
        )
        return labels.get(picked, fallback_slot)

    def tied_learn(self, prompt, tied, values, fallback_slot, *,
                   coverage_gaps=(), owner_types=()):
        """Which move to forget, when the arithmetic cannot separate them.

        Only reached when two or more candidates score IDENTICALLY under the
        damage-aware ranking -- a certain answer is never handed to a 4B model.

        The context is entirely facts read out of the ROM and the live party:
        the mon, its types, every move with its type, base power and the
        effective value the harness computed, and the team's uncovered types.
        Nothing is paraphrased and nothing is invented, because the model's
        job here is a preference between equals, not a calculation. The reply
        is schema-constrained to the offered slots, so an answer naming a move
        that is not on the list cannot come back.
        """
        if len(tied) < 2:
            self.declined += 1
            self.last_reason = "the maths already separated them"
            return fallback_slot

        by_label = {}
        for m in tied:
            label = f"{m['name']} (slot {m['slot']})"
            by_label[label] = m["slot"]
        options = sorted(by_label)
        fallback_label = next(
            (l for l, sl in by_label.items() if sl == fallback_slot),
            options[0],
        )

        new = prompt["new_move"]
        lines = [
            f"{prompt['nickname']} is a {'/'.join(owner_types) or 'unknown'} "
            f"type and wants to learn {new['name']} "
            f"({new.get('type', '?')}, {new.get('power', 0)} power).",
            "Its current moves, with the value the damage maths gives each:",
        ]
        for m in prompt["current"]:
            lines.append(
                f"  {m['name']} -- {m.get('type', '?')}, "
                f"{m.get('power', 0)} base power, "
                f"value {values.get(m['slot'], 0):.0f}"
                + (" (HM, cannot be forgotten)" if m.get("hm") else "")
            )
        lines.append(
            "The listed candidates are equally good by that maths, so pick "
            "the one worth LEAST to this team."
        )
        if coverage_gaps:
            lines.append(
                "Types the team currently cannot hit well: "
                + ", ".join(coverage_gaps)
                + ". Forgetting the only move of a type the team needs is bad."
            )

        picked = self._ask(
            "learn", f"Which move should {prompt['nickname']} forget?",
            options, fallback_label, context="\n".join(lines),
            timeout=self.BATTLE_TIMEOUT,
        )
        return by_label.get(picked, fallback_slot)

    def next_target(self, candidates, fallback=None):
        """Which equally-accessible dex target to sweep next.

        `candidates` are dicts with at least `species` and `area`; they are
        expected to be pre-filtered to comparable accessibility by the dex
        planner, so any pick is a reasonable pick.
        """
        if not candidates:
            return None
        labels = {f"{c['species']} at {c['area']}": c for c in candidates}
        fb = fallback or candidates[0]
        fb_label = next(
            (k for k, v in labels.items() if v is fb), sorted(labels)[0]
        )
        picked = self._ask(
            "next-target",
            "All of these are about equally reachable. Which should the run "
            "go for next?",
            sorted(labels),
            fallback=fb_label,
        )
        return labels.get(picked, fb)

    def preferred_catch(self, candidates, fallback=None):
        """Which catch to prefer when the coverage gain is equal.

        The team policy decides WHETHER a candidate helps; this only picks
        among ones it rated the same.
        """
        if not candidates:
            return None
        labels = {str(c): c for c in candidates}
        fb = fallback or candidates[0]
        fb_label = next((k for k, v in labels.items() if v is fb), sorted(labels)[0])
        picked = self._ask(
            "preferred-catch",
            "These candidates fill the team's type gaps equally well. Which "
            "would make the better team member?",
            sorted(labels),
            fallback=fb_label,
        )
        return labels.get(picked, fb)

    # ---- helpers ------------------------------------------------------------

    @staticmethod
    def _battle_context(analysis):
        me = analysis.get("me") or {}
        foe = analysis.get("enemy") or {}
        return (
            f"my {me.get('name','?')} L{me.get('level','?')} "
            f"{me.get('hp','?')}/{me.get('max_hp','?')} vs "
            f"{foe.get('name','?')} L{foe.get('level','?')} "
            f"{foe.get('hp','?')}/{foe.get('max_hp','?')}"
        )

    def stats(self):
        return {
            "consulted": self.consulted,
            "declined": self.declined,
            "enabled": self.enabled,
        }
