"""Shared E4 battle policy factory (Battle.me()-style dicts)."""


def make_policy(names, allow_items=True, prefer=("FLAMETHROWER",
                                                 "STRENGTH", "SWIFT", "CUT")):
    """names: crystalagent Names (moves id->name). Returns policy fn."""

    def e4_policy(rows, me, enemy):
        # index within the ORIGINAL move list matters for attack(slot)
        idx_by_name = {}
        for i, (mid, pp) in enumerate(me.get("moves", [])):
            nm = names.moves.get(mid, "")
            if nm and nm not in idx_by_name:
                idx_by_name[nm] = (i, pp)
        frac = me["hp"] / max(me["max_hp"], 1)
        if allow_items and frac < 0.55:
            return ("item", "HYPER POTION")
        for nm in prefer:
            if nm in idx_by_name and idx_by_name[nm][1] > 0:
                return ("attack", idx_by_name[nm][0])
        return None

    return e4_policy
