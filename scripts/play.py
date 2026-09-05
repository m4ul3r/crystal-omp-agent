#!/usr/bin/env python
"""The autonomous play loop: every policy the run has, in one place.

This is where the nine requirements actually meet:

* the **objective** is derived from live state, badges then Pokedex;
* the **team** is kept well-rounded and level-parity is enforced as a floor,
  with the training policy giving laggards the KO because Gen-3 splits
  experience among participants;
* the **dex planner** says what to go and get, from the regional-dex-buddy
  catch data plus the ROM's own evolution table;
* the **local model** breaks ties the maths declares equal and nothing else;
* it **saves often**, on a periodic ring plus permanent milestone files;
* it **publishes** the whole picture to the live feed for the widget.

Deliberately a loop over explicit decisions rather than a scripted route: the
harness's own doctrine is that code executes and policy decides, and a fixed
route would be the wrong shape the first time a wanderer stood in a doorway.

    scripts/play.py --state saves/lab.state --minutes 10
"""

import argparse
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pokeagent import team as teammod  # noqa: E402
from pokeagent import jitter  # noqa: E402
from pokeagent.brain import Brain  # noqa: E402
from pokeagent.catching import Catcher  # noqa: E402
from pokeagent.mart import Mart  # noqa: E402
from pokeagent.metrics import Metrics  # noqa: E402
from pokeagent import settings as usersettings  # noqa: E402
from pokeagent.partyorder import PartyOrder  # noqa: E402
from pokeagent.teaching import Teacher  # noqa: E402
from pokeagent.quest import Quest  # noqa: E402
from pokeagent.live import LiveFeed  # noqa: E402
from pokeagent.objective import Autosave, ObjectiveEngine  # noqa: E402
from pokeagent.watchdog import progress_signature  # noqa: E402
from pokeagent import paths  # noqa: E402
from pokeagent.trek import Driver, TravelInterrupted  # noqa: E402

log = logging.getLogger("play")

#: Heal when the lead drops below this fraction of its max HP.
HEAL_AT = 0.45
#: How many times to retry one story step before training instead.
STORY_ATTEMPTS = 6

#: How many times to retry one destination before training instead.
TRAVEL_ATTEMPTS = 8

#: Seconds between reconsidering WHERE to grind. Routing every candidate map is
#: not free, and the answer changes only when the party or the dex does.
GRIND_REVIEW = 240.0

#: Restock when the ball pocket falls to this, so the catcher's own reserve is
#: never the thing that stops a team being built.
#: What a party should be carrying, in the order money is spent on it.
#: (item, restock when below, top up to). Names are the ROM's own strings.
#:
#: There is no ETHER or ELIXIR here on purpose: grepping every `pokemart`
#: list in the decomp finds ZERO of them, so PP cannot be bought in Hoenn at
#: all. PP comes from a Centre nurse (free, full, and now a trigger in its
#: own right) or from Ethers found on the ground. Buying is not an option the
#: game offers, and pretending otherwise would just fail at the counter.
SUPPLIES = (
    # Healing, best tier first: a 20-HP Potion is noise to a level-40 party,
    # and the run carries money it never spends. Anything a given shop does
    # not stock is skipped rather than failed at (see `restock`), so this one
    # list covers a village Mart and the Lilycove department store alike --
    # the store simply fills more of it.
    ("SUPER POTION",  6, 12),
    ("HYPER POTION",  4,  8),   # Lilycove 2F and the later Marts
    ("REVIVE",        2,  4),
    ("MAX POTION",    0,  2),   # expensive; a small emergency reserve
    ("FULL HEAL",     2,  4),   # one item for every status
    ("POTION",        2,  6),   # cheap filler, still worth having early
    # Single-status cures: cheaper than Full Heal, so they are the bulk answer
    # and Full Heal is the convenience. Burn and Ice only appear at the
    # bigger shops, which is fine -- they are skipped where absent.
    ("ANTIDOTE",      1,  2),
    ("PARALYZE HEAL", 1,  2),
    ("AWAKENING",     1,  2),
    ("BURN HEAL",     1,  2),
    ("ICE HEAL",      1,  2),
)

#: Never spend the wallet to zero -- balls and the next Centre trip matter
#: more than a full medicine bag.
SUPPLY_BUDGET_FRACTION = 0.5

#: Wall-clock seconds one journey may own before it hands the loop back.
#: Long enough to cross a map with encounters, short enough that the
#: autosave, the feed and the stall watchdog all keep running.
TRAVEL_BUDGET_S = 45.0

MIN_BALLS = 6
#: How many to buy, budget permitting.
BUY_BALLS = 20

#: Stop grinding a route once the party's spread is inside the parity band.
PARITY_BAND = 3


class Session:
    def __init__(self, state, minutes, game="sapphire", use_brain=True,
                 feed_name="default", session="play"):
        self.brain = Brain() if use_brain else None
        self.session = session
        self.state_path = str(state)
        self.d = Driver(state, game=game, brain=self.brain)
        self.objective = ObjectiveEngine(self.d)
        self.quest = Quest(self.d)
        self.autosave = Autosave(self.d, session=session)
        self.team = teammod.Team(self.d.names, self.d.consts, self.d.state)
        self.catcher = Catcher(self.d, self.team)
        #: Last catch note, so a standing policy consulted every turn does not
        #: repeat itself once per turn in the journal.
        self._last_catch_note = None
        self._last_intro_try = 0.0
        self._last_intro_note = None
        # Every battle, including the ones a journey starts.
        self.d.battle_policy = self.standing_policy()
        self.mart = Mart(self.d)
        self.order = PartyOrder(self.d)
        self.teacher = Teacher(self.d)
        #: machine -> the party shape it was refused against, so the same
        #: refusal is announced once rather than every two minutes.
        self._teach_refused = {}
        self.settings = usersettings.load()
        self.heal_at = usersettings.heal_below(self.settings["risk"])
        self.safe_lead_hp = usersettings.lead_min_hp(self.settings["risk"])
        self.gym_margin = usersettings.gym_margin(self.settings["risk"])
        self.metrics = Metrics(self.d, session=session)
        self.metrics.record("start", session, once=False,
                            badges=len(self._badges()))
        self._known_badges = len(self._badges())
        self._known_species = self._species_names()
        self._lead_fails = set()
        #: Leads that FAINTED while in front. Rotation skips them, so a mon
        #: too weak for the local grass cannot be promoted into a heal/faint
        #: loop over and over.
        self._fainted_leads = set()
        #: Names already reported as too frail to lead, so the note is said
        #: once rather than every cycle.
        self._too_frail = set()
        #: Where to grind, and when that choice was last reconsidered.
        self._grind_map = None
        self._grind_picked = 0.0
        self._restock_fails = 0
        # REUSE the Driver's feed when it already made one. Publishing became
        # the Driver's default (any save under saves/ attaches one, named after
        # the state's stem), so an unconditional attach here was a SECOND
        # observer on the same emulator and the loop refused to start at all:
        #   "already has tick observer <LiveFeed default ...>; detach it first"
        # One publisher per emulator -- but the name must be the one the widget
        # watches: `--feed default` on saves/sapphire.state used to publish to
        # live/sapphire.* and the bar widget pinned to `default` saw nothing.
        existing = getattr(self.d, "feed", None)
        if existing is not None and existing.name != feed_name:
            existing.detach()
            self.d.feed = existing = None
        self.feed = existing or LiveFeed(feed_name).attach(self.d)
        self.feed.extra["agent"] = self.agent_card()
        #: minutes <= 0 means run until stopped. The user's framing for this
        #: project is an idle game -- something that keeps going in the
        #: background -- and a fixed budget silently ends the run and leaves a
        #: frozen widget that looks stuck. Verified: a 115-minute session
        #: exited cleanly at 07:18 and was still sitting there at 09:28.
        self.forever = minutes <= 0
        self.deadline = None if self.forever else time.time() + minutes * 60
        # Seeded from the jitter stream, not from a constant. A fixed 1234 here
        # meant every run walked the same tiles in the same order -- which, on
        # top of fixed frame waits, is how two people end up with byte-identical
        # Pokemon. POKEAGENT_SEED pins the whole thing when a bug needs
        # reproducing.
        self.rng = random.Random(jitter.frames(1 << 30, 1.0))
        self.battles = 0
        self.steps = 0
        #: An unattended run must be auditable from its log alone. Without a
        #: heartbeat the loop looked identical whether it was fighting its way
        #: across a route or wedged in a replan storm -- the first overnight
        #: attempt printed five lines and then nothing for nine minutes, and
        #: only the emulator's frame counter could tell the two apart.
        self.heartbeat_every = 60.0
        self._last_beat = time.time()
        #: Stall detection. Frames are NOT progress -- a run wedged inside a
        #: menu burns 80k frames a heartbeat while standing still, which is
        #: exactly how one sat on Route 116 for fifteen minutes with field
        #: controls locked and nothing noticing. Position, battle count and
        #: badge count are the signals that actually mean something happened.
        self._stall_key = None
        self._stall_since = time.time()
        self._stall_level = 0
        self._started = time.time()
        self._last_quest_kind = None
        #: Story steps that refused to advance, by name. A gate the loop
        #: cannot open must not cost the whole night: the Petalburg gym ate
        #: five minutes and 375k frames re-entering the same scene, because
        #: nothing counted the failures.
        self._story_tries = {}
        #: Steps and destinations the loop has stopped attempting, mapped to
        #: WHEN it stopped -- not a permanent set. Giving up forever is only
        #: correct if the world never changes, and in this game the whole
        #: point of a gate is that it opens later. A run sat in Granite Cave
        #: for half an hour training, 233 battles deep, because the one step
        #: that led anywhere had been abandoned six attempts earlier.
        self._story_given_up = {}
        #: Only announce a skip once per step, or the log becomes the skip.
        self._last_skipped_story = None
        #: Destinations travel keeps failing to reach, and how many times.
        #: Same wedge as the story steps: without a bound the loop spun on
        #: Route 104 for the rest of a run because Dewford needs a boat.
        self._travel_fails = {}
        self._travel_given_up = {}

    # ---- helpers ---------------------------------------------------------

    def out_of_time(self):
        return self.deadline is not None and time.time() > self.deadline

    def lead(self):
        party = [m for m in self.d.state.party() if not m.is_egg]
        return party[0] if party else None

    def _badges(self):
        try:
            return self.d.state.badges()
        except Exception:  # noqa: BLE001
            return []

    def _species_names(self):
        try:
            return {
                self.d.names.species(m.species)
                for m in self.d.state.party() if not m.is_egg
            }
        except Exception:  # noqa: BLE001
            return set()

    def track_milestones(self):
        """Time anything worth advertising. Cheap: two reads a step."""
        badges = self._badges()
        if len(badges) > self._known_badges:
            for name in badges[self._known_badges:]:
                self.metrics.record("badge", str(name))
            self._known_badges = len(badges)
            if len(badges) >= 8:
                self.metrics.record("elite_four", "eight badges")
        species = self._species_names()
        for name in sorted(species - self._known_species):
            self.metrics.record("species", str(name))
        self._known_species = species

    def note(self, msg):
        log.info("%s", msg)
        self.feed.note(msg)

    def heartbeat(self, force=False):
        """Say where the run is, at most once a minute.

        Reports the FRAME as well as the position, because a loop that is
        stuck in a menu still moves frames while its position stays put, and
        a loop that is genuinely wedged moves neither.
        """
        now = time.time()
        if not force and now - self._last_beat < self.heartbeat_every:
            return
        self._last_beat = now
        d = self.d
        lead = self.lead()
        who = (f"{lead.nickname} L{lead.level} {lead.hp}/{lead.max_hp}"
               if lead else "no party")
        if self.deadline is None:
            budget = f"[{int((now - self._started) // 60):4d}m in]"
        else:
            budget = f"[{max(0, int(self.deadline - now)) // 60:4d}m left]"
        log.info(
            "%s %s%s f%d | %s | %d battles, %d steps",
            budget, d.map_name(), d.pos(), d.emu.frame, who,
            self.battles, self.steps,
        )
        self.watch_for_a_stall()

    #: How long nothing may happen before the loop tries to free itself. Long
    #: enough that a legitimately slow stretch -- a long fight, a Pokecenter
    #: round trip -- is never mistaken for a wedge.
    STALL_AFTER = 300.0

    def watch_for_a_stall(self):
        """Notice that nothing is happening, and try to fix it.

        An unattended run that wedges is worse than one that crashes: a crash
        restarts, a wedge just keeps logging. The recovery escalates because
        the cheap fix handles the common case and the expensive one must not
        fire on a run that was merely thinking.

        Level 1 backs out of whatever screen is up -- that alone would have
        recovered the Route 116 freeze, which was a party summary page nobody
        recognised. Level 2 adds a settle and a forced replan. Level 3 gives
        up on the current objective and re-derives it, on the theory that the
        loop is chasing something unreachable.
        """
        d = self.d
        try:
            # NOT self.battles. A battle counter advances on its own whenever
            # the loop is fighting, which is exactly when the worst stalls
            # happen: a Lottad used STRENGTH on a Grimer for hundreds of turns
            # with both HP bars frozen, and because each turn bumped the
            # counter this key kept changing and the stall detector never
            # fired once. A progress signature may contain nothing that moves
            # by itself.
            key = progress_signature(d)
        except Exception:  # noqa: BLE001 - a read that fails IS a stall symptom
            return
        if key != self._stall_key:
            self._stall_key = key
            self._stall_since = time.time()
            self._stall_level = 0
            return

        stalled = time.time() - self._stall_since
        if stalled < self.STALL_AFTER * (self._stall_level + 1):
            return
        self._stall_level += 1
        log.warning("[stall] nothing has changed in %.0fs at %s -- recovery %d",
                    stalled, key[0], self._stall_level)
        try:
            if self._stall_level == 1:
                # Whatever screen is up, leave it. This alone recovers the
                # Route 116 case in under a second.
                d.advance_scene(40000)
            elif self._stall_level == 2:
                # Still stuck, so the loop's own memory is suspect: a lead it
                # gave up on, or a cached objective kind, keeps it re-choosing
                # the thing that is not working. Forget and re-derive.
                d.advance_scene(40000)
                d.settle()
                self._lead_fails.clear()
                self._last_quest_kind = None
            else:
                # Last resort: forgive the story steps that were refusing, in
                # case the gate opened while the loop had stopped asking.
                self._story_tries.clear()
                self._story_given_up.clear()
                self._travel_given_up.clear()
                self._stall_since = time.time()
                self._stall_level = 0
        except Exception as exc:  # noqa: BLE001 - recovery must not end the run
            log.warning("[stall] recovery %d failed: %s", self._stall_level, exc)

    # ---- the loop --------------------------------------------------------

    def run(self):
        obj = self.objective.current()
        self.note(
            f"objective: {obj.name} -- {obj.detail} ({obj.percent:.0f}%)"
        )
        self.note(f"playing {self.d.spec.name} (gen {self.d.spec.generation})")
        self.note(usersettings.describe(self.settings["risk"]))
        if self.brain:
            self.note(
                f"local model {'available' if self.brain.available() else 'unreachable'};"
                " it breaks ties only"
            )

        while not self.out_of_time():
            try:
                self.step()
            except TravelInterrupted as exc:
                # Movement never silently auto-fights; the loop decides.
                self.note(f"interrupted: {exc}")
                self.fight()
            except Exception as exc:  # noqa: BLE001 - one bad step must not end the run
                log.warning("step failed: %s", exc, exc_info=True)
                self.d.advance_scene(20000)
            written = self.autosave.tick()
            if written:
                # Refresh the working state too. The ring autosaves live under
                # their own names, so without this a crash-restart reloads
                # whatever the file held at launch and repeats hours of play.
                try:
                    self.d.save()
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not refresh the working state: %s", exc)
            self.track_milestones()
            self.publish_projection()
            self.feed.publish()
            self.heartbeat()
        self.finish()

    def escape_menus(self, tries: int = 14) -> bool:
        """Get back to the overworld from ANY menu, and say whether it worked.

        The loop had no recovery for "a menu is open". `advance_scene` knows a
        cutscene from a fade but cannot dismiss a bag popup, so twice in one
        morning the run sat at Route 110 with a GREAT BALL's description box up
        -- "A good BALL with a higher catch rate than a POKe BALL." -- printing
        its status line every cycle, 0 new steps, while every movement press was
        silently refused. From outside: a frozen game. In the log: nothing at
        all, because nothing thought anything was wrong.

        B is the safe key in every list (gotcha 13: blind A in a shop BUYS, and
        in a bag popup it CONFIRMS), so this walks out with B and verifies
        against `scene_active()` rather than counting presses -- the same lesson
        as the Mart exit, where four presses left the description box up and the
        savestate captured a script owning input.
        """
        d = self.d
        for _ in range(tries):
            if not d.scene_active():
                return True
            d.emu.run_sequence("B:4 .:24")
        d.advance_scene(40000)
        return not d.scene_active()

    def step(self):
        d = self.d
        if d.in_battle():
            self.fight()
            return
        if d.scene_active() or d.learn_open():
            # A move-learn prompt can arrive OUTSIDE a battle -- evolving is
            # the case -- and blind A presses answered it by overwriting
            # whichever slot the cursor rested on. It is a decision, so it
            # goes through the same policy the in-battle path uses.
            if d.learn_open():
                self.handle_learn()
                return
            if d.naming_open():
                # ACCEPT THE DEFAULT. Typing here asked the model for a name
                # and then failed to walk the keyboard cursor to the first
                # letter -- "could not move the cursor to 'Z' at (6,3)" -- so
                # the prompt came back, repeatedly, with a ball sprite on it.
                # From outside that is indistinguishable from a stuck loop, and
                # it was reported as one twice. `accept()` presses OK on the
                # buffer the game already filled with the species name: instant
                # and unable to fail. A nickname has no bearing on the dex.
                from pokeagent.naming import NamingScreen

                NamingScreen(d.emu, d.state).accept()
                self.note("accepted the default name for the new arrival")
                return
            if not self.escape_menus():
                self.note("a menu will not close; scene still owns input")
            return

        # Heal on the WHOLE party, not just slot 0. Two reasons, both
        # measured: switching in Gen 3 swaps party slots, so the "lead" after
        # a training switch is whichever mon was sent in -- and a freshly
        # caught L4 sat at 6/17, which is 35.3% and just above the threshold,
        # so the run kept feeding it to L6 wilds and never walked again
        # (37 steps, 22 battles, position frozen). A Centre trip is cheap; a
        # dead team member is not.
        hurt = [
            m for m in d.state.party()
            if not m.is_egg and m.max_hp and m.hp / m.max_hp < self.heal_at
        ]
        # PP is the other way a party dies, and it dies SILENTLY: full HP, no
        # damaging move left. The lead reached exactly that -- PROTECT 0,
        # MUD-SLAP 0, HEADBUTT 0, HARDEN 17 -- and since it was slot 0 it led
        # every encounter, spent three turns on a move with zero power, got
        # retired for changing nothing, and was switched out. Every wild
        # battle, forever. A Centre nurse restores PP, so "cannot damage
        # anything" belongs on the same trip as "hurt".
        dry = [m for m in d.state.party() if self._out_of_offence(m)]
        # ROTATE BEFORE TREKKING TO A NURSE.
        #
        # This branch RETURNS, and `rotate_the_lead` is fifty lines further
        # down, so a frail lead never got swapped out: the L3 in slot 0 ran
        # itself dry in two encounters, the run healed, walked back out, spent
        # the same 3 PP and healed again. Logged `healing: A out of damaging
        # PP` on a loop for half an hour with an L22 starter sitting in slot 2
        # and the dex frozen at 6.
        #
        # Giving rotation the first word costs one cycle and fixes the cause
        # instead of the symptom; if it swaps, the next cycle re-reads a party
        # whose front mon can actually fight.
        if (hurt or dry) and self.rotate_the_lead():
            return
        if hurt or dry:
            why = []
            if hurt:
                why.append(", ".join(
                    f"{m.nickname} {m.hp}/{m.max_hp}" for m in hurt[:3]))
            if dry:
                why.append(", ".join(
                    f"{m.nickname} out of damaging PP" for m in dry[:3]))
            self.note(f"healing: {'; '.join(why)}")
            if not d.heal_at_nearest_center():
                self.note(f"could not heal: {d.last_heal_reason}")
            return

        # Balls are what a team is made of. The run reached level 20 with one
        # Pokemon and an empty ball pocket beside 5,130 in the wallet, because
        # nothing could buy what catching spends.
        if (self._needs_balls() or self._needs_supplies()) and self.restock():
            return

        # What advances the GAME, not just the party. A loop that only grinds
        # wilds is stuck forever: a 15-minute run reached level 19 over 232
        # battles and was still 0/8 badges on Route 101.
        obj = self.quest.next_objective()
        if obj.kind != self._last_quest_kind:
            self.note(f"objective: {obj.detail} [{self.quest.last_reason}]")
            self._last_quest_kind = obj.kind

        # An HM in the bag is not a road opened. CUT sat in the bag for three
        # badges and ROCK SMASH for one, while the only way north out of
        # Mauville stayed shut behind two breakable rocks.
        self.teach_pending_hms()

        # NO OPPORTUNISTIC FISHING HERE. It was tried and REVERTED, and the
        # reason belongs next to the temptation to re-add it.
        #
        # `fish_for_dex` only runs from `grind_step`, so with the objective
        # almost always "the next badge" the run walked past every bank in
        # Hoenn with two rods in the bag. Calling it from `step` fixed that and
        # created something worse: it walks to the bank itself, and that walk
        # plus the bag flow ran every cycle, producing a visible loop of
        # opening the bag over and over -- reported from watching the screen as
        # the run repeatedly offering a Poke Ball to LOTTAD. Measured: a single
        # `fish_for_dex` call sat in the goto to (23,18) for over 280 seconds.
        #
        # Fishing belongs in a bounded collection phase that owns its own
        # travel, not on the hot path of a loop that is trying to win badges.

        # NO PARTY MEANS THE GAME HAS NOT STARTED YET.
        #
        # Everything below this line assumes at least one Pokemon: training,
        # rotation, healing, catching, the gym spine. A brand-new save has
        # none until Birch is rescued on Route 101, so a fresh run used to sit
        # in its own bedroom repeating "no grass here; heading for Route101"
        # while the game pushed it back down the stairs -- the intro refuses to
        # release you until the wall clock is set
        # (data/scripts/players_house.inc:58-70).
        #
        # `to_starter.drive_intro` already knows that whole sequence; it was
        # only ever run by hand to produce test checkpoints. Running it here is
        # what makes "start the game over and watch it play itself" a single
        # command instead of three.
        if not d.state.party():
            if self.play_the_intro():
                return

        # Before anything that might start a fight: make sure the mon in front
        # is the one that needs the exp.
        self.rotate_the_lead()

        if obj.kind == "heal":
            # Remember WHO went down in front. This is the only place that
            # knows the lead fainted, and rotation needs it to stop choosing
            # the same casualty next cycle.
            try:
                first = next((m for m in d.state.party() if not m.is_egg), None)
                if first is not None and not (first.hp or 0):
                    name = str(first.nickname or "")
                    if name and name not in self._fainted_leads:
                        self._fainted_leads.add(name)
                        self.note(f"{name} fainted while leading; it will not "
                                  f"be rotated to the front again this run")
            except Exception:  # noqa: BLE001 - bookkeeping must not stop a heal
                pass
            if not d.heal_at_nearest_center():
                self.note(f"could not heal: {d.last_heal_reason}")
            return
        if obj.kind == "story":
            step = self.quest.pending_story()
            if step is not None and self._still_given_up(
                    self._story_given_up, step.name):
                # FALL THROUGH TO THE GYM, NOT TO TRAINING.
                #
                # This used to drop straight to grinding, so an unreachable
                # prologue step hid the badge behind it forever -- and the
                # badge is usually the thing that MAKES the step reachable.
                # Live: a fresh run reached Rustboro over and over with
                # "trigger the stolen Devon Goods errand" pending, an errand
                # the game does not arm until Roxanne is beaten. It gave up
                # six times per lap and trained, never once entering the gym
                # twenty tiles away. An hour, 0 badges.
                if step.name != self._last_skipped_story:
                    self._last_skipped_story = step.name
                    self.note(f"skipping '{step.detail}' for now "
                              f"(abandoned {self._abandoned_ago(step.name)}); "
                              f"trying the badge instead")
                obj = self.quest.next_objective(skip_story=True)
            elif self.advance_story(obj):
                return
        if obj.kind == "travel":
            if not self._still_given_up(self._travel_given_up, obj.map_name):
                self.head_for(obj.map_name)
                return
            # Unreachable for now (a ferry, a HM, a gate we cannot open):
            # train instead so the night still buys levels and dex entries.
        if obj.kind == "fight_leader":
            self.challenge_leader(obj)
            return

        # "train" and "done" both grind: levels are never wasted, and the
        # post-badge stages (living dex, 100s, shinies) all want encounters.
        party = self.team.party()
        parity = self.team.parity(party, tolerance=PARITY_BAND)
        if parity["laggards"]:
            names = ", ".join(str(l["nickname"]) for l in parity["laggards"])  # noqa: E501
            self.note(f"training laggards ({names}); spread {parity['spread']}")
            # Put the trainee in SLOT 0 before the encounter, not one turn
            # into it. Gen 3 splits exp between everything that participated,
            # and slot 0 is sent out automatically -- so a mid-battle switch
            # arrives too late and the lead banks half. Measured: the policy
            # switched to the laggard on turn 0 of 1416 consecutive battles
            # while logging "sole participant", and the lead finished eleven
            # levels clear of the team it was supposedly training.
            self.promote_trainee(parity["laggards"])
        self.grind_step()

    def handle_learn(self):
        """Answer a pending move-learn prompt, and say what was decided.

        Logged because it permanently changes the mon: the first run to hit
        one silently traded away SCRATCH, its strongest move, and the only
        evidence was the moveset three hundred battles later.
        """
        d = self.d
        prompt = d.battle.learn_prompt()
        if prompt is None:
            d.advance_scene(20000)
            return
        slot = d.battle.default_learn(prompt)
        new = prompt["new_move"]["name"]
        who = prompt["nickname"]
        if slot is None:
            self.note(f"{who} declined {new} (nothing worth forgetting)")
        else:
            old = next(
                (m["name"] for m in prompt["current"] if m["slot"] == slot),
                f"slot {slot}",
            )
            self.note(f"{who} learned {new}, forgetting {old}")
        if not d.battle.handle_learn(on_learn=lambda _p: slot):
            self.note(f"could not answer the {new} prompt; advancing the scene")
            d.advance_scene(20000)

    def _out_of_offence(self, mon) -> bool:
        """True when this mon OWNS a damaging move but has spent all its PP.

        A NURSE MUST BE ABLE TO FIX WHAT THIS REPORTS. The check used to be
        "no damaging move with PP left", which is two different states wearing
        one name:

          * spent -- it has TACKLE and TACKLE is at 0/35. A Centre fixes this.
          * unarmed -- its whole moveset is status. HARDEN at 30/30 is not a
            shortage of PP, and no nurse in Hoenn will add a move to it.

        Conflating them cost a whole run. A caught CASCOON knowing only HARDEN
        sat in the party at full HP and full PP; every cycle called it dry,
        healed, walked out, re-read the same moveset and healed again. It
        logged `healing: A out of damaging PP` for over an hour, at 0 badges
        and dex 6, while the party stood at full health the entire time.

        So this answers ONLY the restorable question, and `_unarmed` answers
        the other one. An unfixable condition must never drive a repeatable
        action -- that is the shape of every infinite loop this loop has had.
        """
        if mon.is_egg or not mon.hp:
            return False
        try:
            spent = False
            for move_id, pp in zip(mon.moves, mon.pp):
                if not move_id or not self.d.names.move_data(move_id).power:
                    continue
                if pp:
                    return False  # it can still hit something
                spent = True
            return spent  # owns damage, all of it at 0 PP -> a nurse helps
        except Exception:  # noqa: BLE001 - an unreadable moveset is not a zombie
            return False

    def _unarmed_named(self, name: str) -> bool:
        """`_unarmed` for a nickname, since promotion works in names."""
        want = str(name).upper()
        try:
            for m in self.d.state.party():
                if str(m.nickname or "").upper() == want:
                    return self._unarmed(m)
        except Exception:  # noqa: BLE001
            return False
        return False

    def _unarmed(self, mon) -> bool:
        """True when this mon's whole moveset is status moves.

        Permanent until it levels into something with power, so it is never a
        reason to travel: it is a reason not to LEAD with it.
        """
        if mon.is_egg or not mon.hp:
            return False
        try:
            for move_id in mon.moves:
                if move_id and self.d.names.move_data(move_id).power:
                    return False
        except Exception:  # noqa: BLE001
            return False
        return True

    def _supply_deficits(self) -> list:
        """[(item, how many to buy)] for everything below its floor.

        Read from the bag every time rather than remembered: items are spent
        in battle, and a stale count is how a run walks past a Mart with an
        empty medicine pocket and 43,000 in the wallet -- which is exactly
        what it was doing.
        """
        try:
            pocket = self.d.state.bag().get("items") or {}
        except Exception:  # noqa: BLE001
            return []
        held = {str(k).upper(): v for k, v in pocket.items()
                if isinstance(v, int)}
        out = []
        for name, floor, target in SUPPLIES:
            have = held.get(name.upper(), 0)
            if have < floor:
                out.append((name, target - have))
        return out

    def _needs_supplies(self) -> bool:
        if self._restock_fails >= 3:
            return False
        try:
            money = self.d.state.money()
        except Exception:  # noqa: BLE001
            return False
        return money >= 600 and bool(self._supply_deficits())

    def _needs_balls(self) -> bool:
        if self._restock_fails >= 3:
            return False
        try:
            balls = sum(
                v for v in (self.d.state.bag().get("poke_balls") or {}).values()
                if isinstance(v, int)
            )
            money = self.d.state.money()
        except Exception:  # noqa: BLE001
            return False
        return balls < MIN_BALLS and money >= 200

    #: The best shop in the game, and it is not a Mart by name. Lilycove's
    #: department store puts balls, every status cure, and the whole potion
    #: tier up to MAX POTION on one floor (2F, two counters). Worth naming so
    #: `nearest_mart` can consider it at all -- a name filter looking for
    #: "Mart" would never find it.
    #:
    #: What it does NOT sell, checked against every `pokemart` list in the
    #: decomp: ETHER and ELIXIR. PP is not purchasable anywhere in Hoenn.
    #: 3F is vitamins (PROTEIN/CALCIUM/IRON/ZINC/CARBOS/HP UP) and 4F is TMs;
    #: both are real opportunities and neither is a supply run.
    DEPARTMENT_STORE = "LilycoveCity_DepartmentStore_2F"

    def nearest_mart(self):
        """The nearest shop we can reach, or None.

        Not just maps ending in `_Mart`: Lilycove's department store is the
        best shop in the game and its name says nothing about marts, so a
        suffix filter walked the run past it to a village counter stocking a
        third as much. Preferred on ties -- reaching it costs the same as any
        other shop on that leg and it fills far more of the list in one visit.
        """
        d = self.d
        here = d.map_name()
        best = None
        for name in d.nav.index:
            if not (name.endswith("_Mart") or name == self.DEPARTMENT_STORE):
                continue
            try:
                legs = d.nav.route_legs(here, d.pos(), name)
            except Exception:  # noqa: BLE001
                continue
            if legs is None:
                continue
            better = (
                best is None
                or len(legs) < len(best[1])
                # Same distance: take the shop with the deeper shelves.
                or (len(legs) == len(best[1])
                    and name == self.DEPARTMENT_STORE)
            )
            if better:
                best = (name, legs)
        return best[0] if best else None

    def clerk_cell(self, mart_map):
        """The counter clerk, from the map's own object list."""
        try:
            info = self.d.nav.info(mart_map)
        except Exception:  # noqa: BLE001
            return None
        for obj in getattr(info, "objects", ()) or ():
            if "MART_EMPLOYEE" in str(obj.get("graphics_id", "")):
                return (int(obj["x"]), int(obj["y"]))
        return None

    def restock(self) -> bool:
        """Buy balls AND medicine. True when the step was spent on it."""
        d = self.d
        mart = self.nearest_mart()
        if mart is None:
            self._restock_fails += 1
            self.note("no Mart within reach; not restocking")
            return False
        if d.map_name() != mart:
            self.note(f"low on supplies; heading for {mart}")
            self.head_for(mart)
            return True
        cell = self.clerk_cell(mart)
        if cell is None:
            self._restock_fails += 1
            self.note(f"no clerk found on {mart}")
            return False
        cell = self.quest.live_cell(mart, cell)
        try:
            d.talk_to(*cell)
        except TravelInterrupted:
            raise
        except Exception as exc:  # noqa: BLE001
            self.note(f"could not reach the clerk: {str(exc)[:70]}")
            self._restock_fails += 1
            return False
        d.settle(120)
        # Talking opens the counter dialogue; one more A brings up BUY/SELL.
        for _ in range(4):
            if self.mart.is_open():
                break
            d.emu.run_sequence("A:4 .:40")
        if not self.mart.is_open():
            self._restock_fails += 1
            self.note("the clerk did not open a shop")
            return False
        money = d.state.money()
        bought = []
        if self._needs_balls():
            want = min(BUY_BALLS, max(1, money // 200))
            if self.mart.buy("POKé BALL", want):
                bought.append(f"{want}x POKé BALL")
            else:
                self.note(f"could not buy balls: {self.mart.last_reason}")

        # Medicine, in priority order, against what THIS shop stocks and can
        # be afforded. A counter visit is the expensive part; buying one thing
        # while standing at it is a wasted trip.
        try:
            stock = {row["name"].upper(): row for row in self.mart.items()}
        except Exception:  # noqa: BLE001
            stock = {}
        budget = int(d.state.money() * SUPPLY_BUDGET_FRACTION)
        for name, qty in self._supply_deficits():
            row = stock.get(name.upper())
            if row is None:
                continue            # this shop does not carry it
            price = row.get("price") or 0
            if price <= 0:
                continue
            afford = min(qty, budget // price)
            if afford <= 0:
                continue
            if self.mart.buy(row["name"], afford):
                bought.append(f"{afford}x {row['name']}")
                budget -= afford * price
            else:
                self.note(f"could not buy {name}: {self.mart.last_reason}")

        if bought:
            self.note(f"restocked {', '.join(bought)}; money {d.state.money()}")
            self.feed.count("purchases")
            self._restock_fails = 0
        else:
            self._restock_fails += 1
            self.note("bought nothing at the counter")
        self.mart.leave()
        d.advance_scene(20000)
        return True

    #: Don't re-open the party menu more often than this. Rotating costs a
    #: menu round trip, and the exp split only changes between encounters.
    ROTATE_EVERY = 90.0

    #: Re-checked at most this often: it reads the bag and the whole party,
    #: and an HM does not appear between two steps.
    TEACH_EVERY = 120.0

    def teach_pending_hms(self) -> bool:
        """Teach any HM sitting in the bag that nobody can use yet.

        Deliberately only HMs. A TM is a real decision -- it is consumed in
        Gen 3 and the move competes for a slot -- while an HM is free, cannot
        be deleted by accident, and is almost always a road that is currently
        shut. `field_moves()` returning None for a machine we own is the exact
        signal, and it is the one that would have saved the Crystal run's
        hour-long walk (gotcha 16).
        """
        now = time.time()
        if now - getattr(self, "_last_teach", 0.0) < self.TEACH_EVERY:
            return False
        self._last_teach = now
        try:
            known = self.d.field_moves()
            owned = self.d.state.bag().get("tms_hms") or {}
        except Exception:  # noqa: BLE001 - a bag read must not end the run
            return False
        taught = False
        for machine in sorted(owned):
            if not machine.startswith("HM"):
                continue
            move = self.teacher.machine_move(self.teacher._item_id(machine))
            if move is None or known.get(move[1]) is not None:
                continue
            if self.teacher.teach(machine):
                self.note(f"taught {move[1]} to {self.teacher.taught_to}")
                taught = True
            else:
                # Worth one line, not a retry storm. The reason is a fact about
                # the PARTY -- nobody can learn it, or the only takers would
                # have to give up a real attack -- and it cannot change until
                # the party does. Remembered against the party's shape so a
                # new catch or a level-up gets a fresh look.
                shape = tuple(sorted(
                    (m.species, tuple(m.moves)) for m in self.d.state.party()))
                if self._teach_refused.get(machine) != shape:
                    self._teach_refused[machine] = shape
                    self.note(f"not teaching {machine}: {self.teacher.last_reason}")
        return taught

    def rotate_the_lead(self) -> bool:
        """Put the furthest-behind mon in front when the party is lopsided.

        This is NOT the training decision, and conflating the two cost the run
        a runaway. Training asks whether to go and grind; the answer is usually
        no, and once laggards were measured against the median it was almost
        always no. Rotation asks who walks in front, and the answer is almost
        always "whoever is behind" -- Gen 3 gives the lead a full share of
        every encounter it participates in, so the mon in slot 0 pulls away
        from the rest for free.

        With rotation gated behind the training list, NINJA went L29 -> L42 in
        fifty minutes while the other five sat at 27. The spread is the
        trigger now, not the laggard threshold.
        """
        now = time.time()
        party = self.d.state.party()
        # THE COOLDOWN IS FOR TUNING, NOT FOR EMERGENCIES.
        #
        # A 90-second gate is right for "who should be earning exp", and
        # completely wrong for "the mon in front cannot fight". With a frail
        # lead the run heals every few seconds, so the cooldown was never
        # expired at the moment it mattered and the rescue below never ran --
        # not one rotation note in twenty minutes of `healing: A out of
        # damaging PP`.
        #
        # So the frail-lead check is evaluated FIRST and is exempt; ordinary
        # laggard rotation still waits its turn.
        urgent = False
        try:
            alive0 = [m for m in party if not m.is_egg and m.level]
            if len(alive0) > 1:
                strongest = max(m.level for m in alive0)
                urgent = alive0[0].level * 2 < strongest
        except Exception:  # noqa: BLE001
            urgent = False
        if not urgent and now - getattr(self, "_last_rotate", 0.0) < self.ROTATE_EVERY:
            return False
        # A FRESH GAME HAS NO POKEMON. `parity` answers `spread: None` for an
        # empty or single-mon party, and comparing that to a tolerance raised
        # TypeError on every step -- so a brand-new run never got out of the
        # moving truck. It failed identically about four hundred times before
        # anyone looked, because `step failed:` is caught and retried.
        #
        # This is the whole "start the game over and watch it play itself"
        # scenario, and it was broken at frame 7608 of 0.
        if not party:
            return False
        parity = self.team.parity(party)
        spread = parity.get("spread")
        # RESCUE A FRAIL LEAD FIRST.
        #
        # The level floor below stops a frail mon being rotated INTO the front,
        # but nothing moved one OUT of it -- and slot 0 is where a caught mon
        # lands. So the run sat with `lead=A L3` in front of an L22 starter,
        # fainting to every encounter, and the floor changed nothing because
        # rotation was never the thing putting it there.
        #
        # If whoever is in front cannot survive beside the party's best, put
        # the best in front instead. Training resumes once the gap closes.
        alive = [m for m in party if not m.is_egg and m.level]
        if alive:
            lead0 = alive[0]
            best = max(alive, key=lambda m: m.level)
            if best is not lead0 and lead0.level * 2 < best.level:
                self._last_rotate = now
                name = str(best.nickname or "")
                self.note(f"lead {lead0.nickname} L{lead0.level} is too frail "
                          f"beside L{best.level}; putting {name} in front")
                return self.promote_trainee([{"index": None,
                                              "nickname": name,
                                              "level": best.level,
                                              "gap": 0}])
        if spread is None or spread <= teammod.DEFAULT_TOLERANCE:
            return False
        target = self.team.furthest_behind(party)
        lead = next((m for m in party if not m.is_egg), None)
        if target is None or lead is None or target.level >= lead.level:
            return False
        # DO NOT PUT A MON IN FRONT THAT CANNOT SURVIVE BEING THERE.
        #
        # Rotation is right in principle -- the lead takes a full share -- but
        # it had no survivability check at all, and a fresh run turned that
        # into a death spiral: heal at the Centre, promote the newly caught L3
        # to the front, walk into grass, get one-shot, objective becomes
        # "heal", repeat. Measured: twenty minutes bouncing between Petalburg
        # and Oldale's Centres with `lead=A L3 0/15`, dex stuck at 4, while an
        # L22 starter sat in slot 2.
        #
        # A mon that fainted while leading is remembered and skipped, which is
        # what `_lead_fails` already does for a mon the party MENU refuses.
        # Same idea, different failure: the menu said no, the game said no.
        if getattr(target, "label", None) in self._fainted_leads:
            return False
        # AND A LEVEL FLOOR, because the name-keyed memory alone is not
        # enough: the catch flow leaves every caught mon nicknamed "A", so
        # three different Pokemon shared one key and the guard could not tell
        # them apart. Level is name-independent.
        #
        # Half the current lead's level is the line. Below that the mon is not
        # being trained, it is being fed to the grass: an L3 in front of an
        # L22's party is one-shot by anything on Route 102, and the run spent
        # twenty minutes healing it.
        if target.level * 2 < lead.level:
            if target.label not in self._too_frail:
                self._too_frail.add(target.label)
                self.note(f"not rotating {target.label} L{target.level} to the "
                          f"front: too frail beside L{lead.level} "
                          f"(it would lead into one-shots)")
            return False
        self._last_rotate = now
        self.note(f"rotating {target.label} L{target.level} to the front "
                  f"(spread {parity['spread']})")
        return self.promote_trainee([{"index": target.index,
                                      "nickname": target.label,
                                      "level": target.level,
                                      "gap": parity["max"] - target.level}])

    def promote_trainee(self, laggards) -> bool:
        """Make the furthest-behind laggard lead, so it takes the whole KO.

        Skipped once a mon has failed to promote, because the party menu is
        driven by press-count search (its cursor is a sprite) and retrying it
        every step would spend the run in a menu.
        """
        # THE FLOOR LIVES HERE, because there are TWO callers and guarding
        # only one achieved nothing. `rotate_the_lead` got the check first,
        # and the training branch calls this directly with
        # `parity["laggards"]` -- so the rescue put EMBER L22 in front
        # (measured: ['A','EMBER',...] -> ['EMBER','A',...]) and the very next
        # cycle's laggard promotion put the L3 straight back. The run then
        # healed, walked out, ran dry and healed again, for hours.
        #
        # A mon below half the party's best is not being trained by leading,
        # it is being one-shot. It still earns a share by participating.
        try:
            best = max((m.level or 0) for m in self.d.state.party()
                       if not m.is_egg)
        except Exception:  # noqa: BLE001
            best = 0
        target = None
        for row in laggards:
            name = str(row.get("nickname") or "")
            if not name or name in self._lead_fails:
                continue
            level = row.get("level") or 0
            if best and level * 2 < best:
                if name not in self._too_frail:
                    self._too_frail.add(name)
                    self.note(f"not promoting {name} L{level}: too frail "
                              f"beside L{best}")
                continue
            # AND IT MUST BE ABLE TO HIT SOMETHING. Leading with a mon whose
            # whole moveset is status hands the run to a wild that cannot be
            # damaged back: it is the training equivalent of the heal loop.
            if self._unarmed_named(name):
                if name not in self._too_frail:
                    self._too_frail.add(name)
                    self.note(f"not promoting {name}: it owns no damaging "
                              f"move, so leading with it wins nothing")
                continue
            target = name
            break
        if target is None:
            return False
        if self.order.index_of(target) == 0:
            return True
        if self.order.lead_with(target):
            self.note(f"{target} leads now, so it takes the full exp")
            self.feed.count("promotions")
            return True
        self._lead_fails.add(target)
        self.note(f"could not promote {target}: {self.order.last_reason}")
        return False

    def hop_toward(self, map_name) -> bool:
        """Fly to the town that gets closest to `map_name`, if flying is on.

        The region map only offers towns, so an interior or a route takes two
        legs: land, then walk. The landing is chosen by MAP-GRAPH distance --
        how many map-to-map hops separate a fly target from the destination --
        and then `travel` does the real, reachability-aware routing from there.
        Using the graph to pick a landing is fine; using it to claim a route is
        the mistake `travel` had its graph fallback removed for, and this does
        not do that.
        """
        d = self.d
        if d.field_moves().get("FLY") is None:
            return False
        try:
            # `fly_destinations()` yields Landing records, not names.
            targets = [
                t.map_name for t in d.fly_destinations()
                if getattr(t, "map_name", None) and t.map_name != d.map_name()
            ]
        except Exception as err:  # noqa: BLE001
            log.debug("no fly destinations: %s", err)
            return False
        if not targets:
            return False
        want = self._graph_distances(map_name)
        best = min(targets, key=lambda t: (want.get(t, 99), t))
        if want.get(best, 99) >= 99 and not map_name.startswith(best):
            return False
        if not d.fly_to(best):
            self.note(f"could not fly to {best}: {d.last_fly_reason}")
            return False
        return True

    def _graph_distances(self, target, max_hops=8) -> dict:
        """Map-to-map hop counts from `target` outward, over warps and seams.

        Cached per target: it is static structure, and the only use is ranking
        fly landings.
        """
        cache = getattr(self, "_graph_cache", None)
        if cache is None:
            cache = self._graph_cache = {}
        if target in cache:
            return cache[target]
        from collections import deque

        d = self.d
        seen = {target: 0}
        queue = deque([(target, 0)])
        while queue:
            here, depth = queue.popleft()
            if depth >= max_hops:
                continue
            try:
                exits = d.nav.exits(here)
            except Exception:  # noqa: BLE001 - an unreadable map ends that branch
                continue
            for e in exits:
                dest = e.get("dest")
                if not dest or dest in seen:
                    continue
                seen[dest] = depth + 1
                queue.append((dest, depth + 1))
        cache[target] = seen
        return seen

    def head_for(self, map_name):
        """Move one leg toward a destination, fighting what interrupts.

        `travel` raises rather than auto-fighting, so a journey across grass
        is a sequence of legs punctuated by encounters -- which is fine, and
        is also how the party gets the levels the gym needs.
        """
        d = self.d
        try:
            # FLY FIRST when the walk has already failed here. Hoenn is wide
            # and the walker is honest: Route 119 back to Route 118 means
            # crossing a river, and the run reported "could not cross the D
            # seam" eight times in a row while a Pelipper stood in the party
            # knowing FLY. A hop turns those journeys into two legs -- land at
            # the nearest town, walk the rest -- and costs nothing when the
            # walk was working.
            if self._travel_fails.get(map_name, 0) >= 1 and self.hop_toward(map_name):
                self.note(f"flew toward {map_name}, now at {d.map_name()}")
            # Bounded so one journey cannot own the loop: a Route 119 crossing
            # spent 400+ seconds inside a single step(), which meant no
            # autosave, no feed update and a blind watchdog for the duration.
            # An unfinished journey simply resumes next cycle.
            if d.travel(map_name, on_battle="fight", budget_s=TRAVEL_BUDGET_S):
                self.note(f"arrived at {map_name}")
                self._travel_fails.pop(map_name, None)
        except TravelInterrupted:
            raise
        except Exception as exc:  # noqa: BLE001
            if self.scripted_transport(map_name, str(exc)):
                return
            n = self._travel_fails.get(map_name, 0) + 1
            self._travel_fails[map_name] = n
            self.note(f"could not reach {map_name} ({n}): {str(exc)[:90]}")
            if n >= TRAVEL_ATTEMPTS:
                self._travel_given_up[map_name] = time.time()
                self.note(
                    f"giving up on {map_name} after {n} tries; "
                    f"{self.gate_hint(d.map_name())} -- training instead"
                )
                self.note(self.blocker_hint(dest_map=map_name))
            d.advance_scene(20000)

    #: Maps the walking router can NEVER reach, and the scripted ride that
    #: does. The Lavaridge side of Route 112 is entered exactly one way going
    #: up -- descend Jagged Pass from Mt. Chimney -- and the way onto the
    #: mountain is the cable car, which is an NPC conversation, not a warp.
    #: route_legs cannot plan through it, so "no walkable route" to any of
    #: these maps means "ride first", not "give up and train".
    CABLE_CAR_SIDE = frozenset((
        "MtChimney", "JaggedPass", "LavaridgeTown", "LavaridgeTown_Gym_1F",
        "LavaridgeTown_Gym_B1F", "LavaridgeTown_PokemonCenter_1F",
        "LavaridgeTown_Mart", "LavaridgeTown_HerbShop",
        "LavaridgeTown_House", "MtChimney_CableCarStation",
    ))

    def scripted_transport(self, map_name, why) -> bool:
        """Ride whatever scripted vehicle reaches an unroutable destination.

        Returns True when a ride was taken (the caller should simply retry
        its journey next cycle -- from the far side, the router works again).
        """
        if "no walkable route" not in why:
            return False
        if map_name not in self.CABLE_CAR_SIDE:
            return False
        d = self.d
        self.note(f"{map_name} is across the cable car; riding")
        try:
            if d.map_name() != "Route112_CableCarStation":
                d.travel("Route112_CableCarStation", on_battle="fight")
            if d.map_name() != "Route112_CableCarStation":
                return False
            d.talk_to(6, 6)
            if d.choice_open():
                d.resolve_choice("YES")
            d.advance_scene(120000)
            d.settle(600)
            rode = d.map_name() == "MtChimney_CableCarStation"
            if rode:
                self.note("rode the cable car up Mt. Chimney")
            return rode
        except TravelInterrupted:
            raise
        except Exception as exc:  # noqa: BLE001 - the ride failing falls back
            self.note(f"cable car ride failed: {str(exc)[:80]}")
            return False

    #: How long an abandoned step stays abandoned. Long enough that the loop
    #: does something useful in between -- training is not wasted -- and short
    #: enough that a gate which has since opened is noticed the same session.
    GIVE_UP_FOR = 600.0

    def _abandoned_ago(self, key) -> str:
        when = self._story_given_up.get(key) or self._travel_given_up.get(key)
        return "just now" if not when else f"{(time.time() - when) / 60:.0f} min ago"

    def _still_given_up(self, book, key) -> bool:
        """Is this thing still off the table?

        Expiry, rather than a permanent set, because the reason a step failed
        is usually temporary: a battle interrupted the walk, an NPC was mid
        script, a flag had not been set yet. The failure that motivated this
        was the sail to Slateport -- abandoned while the letter that unlocks
        it was still undelivered, and never reconsidered once it was.
        """
        when = book.get(key)
        if when is None:
            return False
        if time.time() - when < self.GIVE_UP_FOR:
            return True
        del book[key]
        self.note(f"reconsidering {key} after {self.GIVE_UP_FOR / 60:.0f} min")
        if self._last_skipped_story == key:
            self._last_skipped_story = None
        self._story_tries.pop(key, None)
        self._travel_fails.pop(key, None)
        return False

    def advance_story(self, obj):
        """Clear a prologue gate: go to the map, then talk or just arrive.

        The gates themselves come from the game's map data (see
        pokeagent/gates.py), so a failure here reports the variable that is
        still shut rather than a pathing error.
        """
        d = self.d
        step = self.quest.pending_story()
        if step is None:
            return False
        if d.map_name() != step.map_name:
            # HONOUR THE GIVE-UP. Only the `travel` objective consulted
            # _travel_given_up; the story path called head_for unconditionally
            # every cycle, so a step whose map is unreachable retried forever
            # -- the live run logged "could not reach
            # Route119_WeatherInstitute_2F (8125)" against a bridge deck it
            # cannot board. Returning False lets the caller train instead,
            # which is what the give-up was for.
            if self._still_given_up(self._travel_given_up, step.map_name):
                if step.map_name != self._last_skipped_story:
                    self._last_skipped_story = step.map_name
                    self.note(f"{step.map_name} is off the table for now "
                              f"({self._abandoned_ago(step.map_name)}); "
                              f"training instead")
                return False
            self.head_for(step.map_name)
            return True
        stand = getattr(step, "stand", None)
        if stand is not None:
            # A coord_event fires when you step ON it, so walking adjacent and
            # facing -- what talk_to does -- never triggers it.
            try:
                if not d.goto(*stand, on_battle="fight"):
                    self.note(f"could not stand on {stand}: {d.last_goto_reason}")
            except TravelInterrupted:
                raise
            except Exception as exc:  # noqa: BLE001
                self.note(f"could not stand on {stand}: {str(exc)[:80]}")
        if step.talk is not None:
            # Live position, not the map file's: scripts move people, and
            # Norman stands 104 tiles from his map entry during his own intro.
            cell = self.quest.live_cell(step.map_name, step.talk)
            if cell != step.talk:
                self.note(f"{step.map_name} object moved to {cell}")
            try:
                d.talk_to(*cell)
            except TravelInterrupted:
                raise
            except Exception as exc:  # noqa: BLE001
                self.note(f"could not reach {step.talk}: {str(exc)[:80]}")
        # Some of these gates end in a question -- Mr. Briney asks whether to
        # set sail -- and an unanswered choice box stops the scene dead. It is
        # answered explicitly rather than left to the stall-press.
        self.answer_the_question(step)
        # Either way, let the map's own scripts run: several of these gates are
        # cleared by an ON_FRAME script that fires just from standing there.
        d.advance_scene(120000)
        # PASSIVE this time. The first call is allowed to press A to bring a
        # box up; a second one that does the same mashes sixteen presses into
        # whatever cutscene the first answer just started. That is not
        # theoretical -- it cancelled the sail to Slateport on every single
        # attempt, and the log reads "chose SLATEPORT" immediately followed by
        # "could not pick 'SLATEPORT'", over and over.
        if self.answer_the_question(step, press=False):
            d.advance_scene(120000)
        if self.quest.pending_story() is not step:
            self.note(f"story advanced: {step.detail}")
            self._story_tries.pop(step.name, None)
            return True

        n = self._story_tries.get(step.name, 0) + 1
        self._story_tries[step.name] = n
        if n >= STORY_ATTEMPTS:
            self._story_given_up[step.name] = time.time()
            self.note(
                f"giving up on '{step.detail}' after {n} attempts; "
                f"{self.gate_hint(step.map_name)} -- training instead"
            )
            return False
        return True

    def answer_the_question(self, step, press=True) -> bool:
        """Answer an open choice box the way this step needs it answered.

        Most gates end in YES/NO. Mr. Briney's does not: after the letter is
        delivered he asks "Where are we bound?" and offers PETALBURG /
        SLATEPORT / CANCEL (MultichoiceList_00, script_menu.c:17). A generic
        YES takes the first option and sails the run BACK to the mainland it
        just left, which reads as progress in the log and is the opposite.

        Returns whether anything was answered, so the caller only pays for
        another scene advance when there was something to advance.
        """
        from pokeagent.menus import Menus

        d = self.d
        wanted = getattr(step, "choice", None)
        if not wanted and not d.choice_open():
            return False
        if wanted:
            menus = Menus(d.emu, d.state)
            # NOT gated on choice_open(): gMenu's bounds are leftovers until
            # the box is drawn, so it answers True while a message box is
            # still printing. select_label advances to the real box itself.
            if menus.select_label(wanted, among=getattr(step, "choice_among", None),
                                  press=press):
                self.note(f"chose {wanted}")
                return True
            # Naming an option and not finding it is worth saying out loud;
            # falling through to YES silently is how you end up in Petalburg.
            # Only worth saying when we actually expected a box. The passive
            # follow-up probe not finding one is the normal case.
            if press:
                self.note(f"could not pick '{wanted}': {menus.last_reason}")
            return False
        d.resolve_choice("YES")
        return True

    #: How often the finish estimate is recomputed. It reads the whole event
    #: log, and it only changes when a badge or a species lands, so once a
    #: minute is already generous.
    PROJECTION_EVERY = 60.0

    def publish_projection(self):
        """Put the "how long will this take" numbers in the feed, and refresh
        the agent card beside them.

        The point of the run is partly the claim it can support -- "N hours of
        idle time to beat the game" -- and a number nobody can see is a number
        nobody will check. It ships WITH its basis string, so the widget can
        show how thin the evidence is rather than quoting a lone figure from
        two data points.
        """
        now = time.time()
        if now - getattr(self, "_last_projection", 0.0) < self.PROJECTION_EVERY:
            return
        self._last_projection = now
        try:
            self.feed.extra["projection"] = self.metrics.projection()
            self.feed.extra["totals"] = self.metrics.summary()
            self.feed.extra["agent"] = self.agent_card()
        except Exception as exc:  # noqa: BLE001 - a metric must never stop play
            log.debug("projection failed: %s", exc)

    def agent_card(self) -> dict:
        """What is driving this run, for the widget: the session, the risk it
        runs at, and the local model it consults -- with whether that model
        is actually answering, because "gemma4:e4b" on a card means nothing
        if every decision has been falling back to the maths for an hour.

        The feed adds the process identity (script, pid, host, uptime) itself.
        ``Brain.available()`` is a probe cached for its own TTL, so refreshing
        this once a minute costs one ``/api/tags`` at most.
        """
        card = {
            "session": self.session,
            "state": self.state_path,
            "risk": self.settings["risk"],
            "risk_label": usersettings.mood(self.settings["risk"]),
        }
        if self.brain is None:
            card["model_state"] = "off"
            return card
        card["model"] = self.brain.model
        card["model_host"] = self.brain.host
        card["model_state"] = "ready" if self.brain.available() else "unreachable"
        card["model_reason"] = self.brain.last_reason
        stats = self.brain.stats()
        card["decisions"] = {k: stats.get(k, 0) for k in ("hits", "fallbacks", "timeouts")}
        return card

    def blocker_hint(self, dest_map=None, warp=None) -> str:
        """Why a road is shut, named from the map data.

        Four of the five gates that have stalled this run were an OBJECT on
        the only approach to a door, and each cost half an hour of hand
        diagnosis while the pathfinder was telling the truth. Printing the
        answer next to the failure turns that into a log line.
        """
        try:
            from pokeagent.blockers import Blockers

            return Blockers(self.d).explain(dest_map=dest_map, warp=warp)
        except Exception as exc:  # noqa: BLE001 - a hint must never raise
            return f"blocker lookup failed: {exc}"

    def gate_hint(self, map_name):
        """Whatever the map data can say about why a step is stuck."""
        try:
            from pokeagent.gates import GateReader

            x, y = self.d.pos()
            return (GateReader(self.d.state).explain(map_name, x, y)
                    or "no live gate found there")
        except Exception as exc:  # noqa: BLE001
            return f"gate lookup failed: {exc}"

    #: Longest switch sequence worth trying. Three switches give 3 + 6 + 6 =
    #: 15 candidate sequences at depth 3, which is seconds of emulation and
    #: covers every Sapphire barrier room.
    MAX_SWITCH_DEPTH = 3

    def floor_switches(self, map_name) -> list:
        """Switch cells, from the map's own coord_events.

        Read from the data rather than a table here, so a barrier room nobody
        has looked at works the same way.
        """
        found = []
        try:
            triggers = self.d.nav.info(map_name).triggers or []
        except Exception:  # noqa: BLE001
            triggers = []
        found += [(t["x"], t["y"]) for t in triggers
                  if "switch" in str(t.get("script", "")).lower()]

        # A SWITCH YOU PRESS is not a switch you step on. Mauville's are
        # coord_events, so reading only those was right there and blind
        # everywhere else: Mossdeep's four are `bg_events` of type "sign" at
        # (2,7), (8,10), (17,15) and (5,24)
        # (pret/data/maps/MossdeepCity_Gym/map.json), pressed with A while
        # facing them. `floor_switches` returned an empty list for that gym, so
        # `press_floor_switches` had nothing to try and the barrier branch was
        # a no-op -- while the floor's 173 arrows stayed pointed the wrong way.
        import json as _json

        try:
            j = _json.loads((paths.MAPS / map_name / "map.json").read_text())
        except Exception:  # noqa: BLE001
            return found
        for b in j.get("bg_events", ()) or ():
            if "switch" not in str(b.get("script", "")).lower():
                continue
            cell = (int(b["x"]), int(b["y"]))
            if cell not in found:
                found.append(cell)
        return found

    def press_floor_switches(self, map_name, target) -> bool:
        """Open a barrier puzzle by SEARCHING it, not by guessing.

        Mauville's gym is a maze of electric barriers worked by three floor
        switches, and the .blk files describe the map as shipped -- so the
        pathfinder sees the leader in a component it cannot enter and reports,
        correctly, that there is no route.

        The obvious approach, pressing each switch in turn, does not work and
        it took a while to see why: the switches are TOGGLES. Pressing the
        first one rearranges the room, so the second is tried against a map
        the first just changed, and the useful state is gone. Undoing is not
        reliable either -- the player has walked off the switch by then, and a
        gym trainer standing in the corridor can make walking back impossible.

        So fork and search, which is what this harness is FOR: same state plus
        same inputs is byte-identical, so every trial is exact and a failed
        one costs nothing. Try sequences in increasing length, keep the first
        that makes the target reachable, and reload the fork between attempts
        so each one starts from the same room.
        """
        d = self.d
        switches = self.floor_switches(map_name)
        if not switches:
            return False
        if target in d.nav.reachable(map_name, d.pos()):
            return True

        import itertools
        import tempfile

        scratch = Path(tempfile.mkdtemp()) / "puzzle.state"
        # `Driver.load` repoints `state_path`, so every load below would move
        # the run's save target into a temp directory -- and the working state
        # would stop advancing while the log kept saying it had. Remember it
        # and put it back on every exit path.
        working = d.state_path
        d.save(scratch)

        def try_sequence(seq) -> bool:
            d.load(scratch)
            d.nav.clear_live_cells(map_name)
            for cell in seq:
                try:
                    # A SIGN CANNOT BE STOOD ON. Mauville's switches are floor
                    # panels, so walking onto the cell IS the press; Mossdeep's
                    # four are `bg_events` of type "sign", which are solid --
                    # `goto` onto one can only ever fail. `talk_to` walks
                    # adjacent, faces it and presses A, which is the press for
                    # both kinds, so try that first and fall back to standing
                    # on it for the panels.
                    pressed = False
                    try:
                        pressed = bool(d.talk_to(*cell))
                    except TravelInterrupted:
                        raise
                    except Exception:  # noqa: BLE001
                        pressed = False
                    if not pressed and not d.goto(*cell, on_battle="fight"):
                        return False
                except TravelInterrupted:
                    raise
                except Exception:  # noqa: BLE001 - a failed trial is just a no
                    return False
                d.settle()
                d.advance_scene(20000)
                d.sync_grid()
            return target in d.nav.reachable(map_name, d.pos())

        for depth in range(1, self.MAX_SWITCH_DEPTH + 1):
            for seq in itertools.permutations(switches, depth):
                try:
                    if try_sequence(seq):
                        self.note(f"barrier puzzle solved: {' -> '.join(map(str, seq))}")
                        d.state_path = working
                        return True
                except TravelInterrupted:
                    # A battle mid-trial makes this trial meaningless, but the
                    # emulator is now in it and the loop has to resolve it.
                    d.state_path = working
                    raise
        # Nothing worked: put the room back the way it was rather than leaving
        # it in whatever state the last failed trial produced.
        d.load(scratch)
        d.state_path = working
        d.nav.clear_live_cells(map_name)
        d.sync_grid()
        self.note(f"no switch sequence up to {self.MAX_SWITCH_DEPTH} opened the way")
        return False

    def fight_the_room(self, map_name) -> bool:
        """Battle every reachable gym trainer, then re-enter to unlock doors.

        Returns True when at least one battle happened (progress), False when
        the room offered nobody -- which means the blocker is not a trainer.
        """
        import json as _json

        d = self.d
        try:
            j = _json.loads((paths.MAPS / map_name / "map.json").read_text())
        except Exception:  # noqa: BLE001
            return False
        fought = 0
        for o in j.get("object_events", ()):
            if o.get("trainer_type") not in ("TRAINER_TYPE_NORMAL",
                                             "TRAINER_TYPE_SEE_ALL_DIRECTIONS"):
                continue
            cell = self.quest.live_cell(map_name, (o["x"], o["y"]))
            r = d.nav.reachable(map_name, d.pos(), d.elevation())
            if not any((cell[0] + dx, cell[1] + dy) in r
                       for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))):
                continue
            before = self.battles
            try:
                d.talk_to(*cell)
            except TravelInterrupted:
                self.fight()
            except Exception as exc:  # noqa: BLE001
                self.note(f"could not engage the trainer at {cell}: "
                          f"{str(exc)[:70]}")
                continue
            if d.in_battle():
                self.fight()
            d.advance_scene(60000)
            if self.battles > before or True:
                fought += 1
        if not fought:
            return False
        # Doors are applied by OnLoad, which runs on entering the map. Step
        # out through the nearest exit warp and come straight back.
        self.note(f"fought the room ({fought} trainers); re-entering "
                  f"{map_name} to unlock its doors")
        try:
            exits = [w for w in d.nav.info(map_name).warps
                     if d.nav.const_to_name(w.dest_map) != map_name]
            r = d.nav.reachable(map_name, d.pos(), d.elevation())
            for w in exits:
                near = (w.x, w.y) in r or any(
                    (w.x + dx, w.y + dy) in r
                    for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)))
                if not near:
                    continue
                if d._enter_warp(w.x, w.y, on_battle="fight"):
                    outside = d.map_name()
                    back = [ww for ww in d.nav.info(outside).warps
                            if d.nav.const_to_name(ww.dest_map) == map_name]
                    if back:
                        d._enter_warp(back[0].x, back[0].y, on_battle="fight")
                    return True
        except Exception as exc:  # noqa: BLE001
            self.note(f"re-entry failed: {str(exc)[:70]}")
        return True

    def _nearest_adjacent(self, cell):
        """The cell beside `cell` closest to the player, for a first attempt."""
        px, py = self.d.pos()
        return min(self._adjacent_to(cell),
                   key=lambda c: abs(c[0] - px) + abs(c[1] - py))

    def challenge_leader(self, obj):
        """Walk up to the leader and talk. Their script starts the battle.

        The gym's own trainers may intercept on the way in; that is what
        `talk_to` raising into the loop's battle handling is for.
        """
        d = self.d
        cell = self.quest.leader_cell(obj.map_name, obj.leader)
        if cell is not None:
            cell = self.quest.live_cell(obj.map_name, cell)
        if cell is None:
            self.note(f"cannot find {obj.leader} on {obj.map_name}")
            self.grind_step()
            return
        # The gym's own doors move: Petalburg rewrites its door metatiles
        # from OnLoad and from each trainer's defeat script, and planning on
        # the static grid walks at pairs that are still shut. Read the truth.
        try:
            d.sync_grid()
        except Exception:  # noqa: BLE001 - a failed sync just means static
            pass
        # SOOTOPOLIS' FLOOR IS NOT WALKABLE BY ROUTING. Every thin-ice tile
        # cracks when you step off it and is a hole on the second visit, so an
        # ordinary path falls through to B1F halfway to Wallace. This loop
        # reported "walk to Wallace failed (left SootopolisCity_Gym_1F for
        # SootopolisCity_Gym_B1F mid-route)" over and over and never once
        # started the battle. The solver covers each section in one pass.
        if obj.map_name == "SootopolisCity_Gym_1F":
            try:
                import ice_run

                # Stand IN FRONT of him, never on him. Aiming at Wallace's
                # own cell (8,2) is unreachable by definition, so the solver
                # crossed all three sections to (8,6), failed the last step
                # and reset the floor -- over and over.
                stand = (cell[0], cell[1] + 1)
                if d.pos() != stand and not ice_run.cross_to(d, *stand):
                    self.note("could not cross the gym ice to "
                              f"{obj.leader}; training instead")
                    self.grind_step()
                    return
            except Exception as exc:  # noqa: BLE001
                self.note(f"ice solver failed: {str(exc)[:70]}")

        # A barrier puzzle looks exactly like a missing route, so try the
        # switches BEFORE walking at a leader we cannot reach.
        if cell not in d.nav.reachable(obj.map_name, d.pos()):
            self.note(f"{obj.leader} is behind a barrier; looking for switches")
            self.press_floor_switches(obj.map_name, cell)
        # Still unreachable is not the same as unreachable-on-foot. Lavaridge's
        # gym floor splits into pockets joined only by falling through holes to
        # B1F and climbing back into a different part of 1F -- no switch, no
        # barrier, just a room that is several components. `reach_cell` routes
        # through the warps; `talk_to` alone can only ever fail there.
        if cell not in d.nav.reachable(obj.map_name, d.pos()):
            self.note(f"{obj.leader} is in another part of {obj.map_name}; "
                      "routing through the floor")
            if not d.reach_cell(*cell, map_name=obj.map_name,
                                on_battle="fight"):
                self.note(f"could not route to {obj.leader}: "
                          f"{d.last_goto_reason}")
                # Third kind of gym: doors gated on DEFEATED TRAINERS.
                # Petalburg's OnLoad opens each room's onward doors
                # call_if_defeated (scripts.inc:154497-15450x), applied on
                # map load. No switch, no maze -- the key is a battle, and
                # the lock re-reads on re-entry. So fight whoever this room
                # offers, step out and back in to re-run OnLoad, and let the
                # next cycle try the leader again.
                if self.fight_the_room(obj.map_name):
                    return
        # ROTATING GATES look like nothing at all. Fortree's gym is seven of
        # them (`special RotatingGate_InitPuzzle`), and they are neither
        # metatiles nor object events -- so `sync_grid` finds no drift, nav
        # reports the leader reachable across 205 cells, every branch above is
        # skipped, and `talk_to` walks into an invisible arm. The run reported
        # "Winona not beaten yet" seven times in a row and then could not even
        # find the exit.
        #
        # nav cannot answer this, so do not ask it: if the map has gates, put
        # the player on a cell BESIDE the leader with the savestate search
        # first. Walking into a gate rotates it, which is the puzzle.
        if d.gate_signature() and cell not in self._adjacent_to(d.pos()):
            for spot in self._adjacent_to(cell):
                if d.pos() == spot:
                    break
                if d.reach_cell(*spot, map_name=obj.map_name,
                                on_battle="fight"):
                    self.note(f"solved {obj.map_name}'s gates to reach {spot}")
                    break
        # A FLOOR CAN TELEPORT YOU, and nav cannot see it. Mossdeep's gym is
        # the case: one LEFT press moved the player from (2,22) to (8,17), so
        # the room is several components joined by tiles that look like
        # ordinary floor. nav therefore reports the leader reachable across 394
        # cells, EVERY escalation above is skipped because they all ask nav
        # first, and `talk_to` walks 144 chunks without arriving -- "challenging
        # TateAndLiza at (8,3)" / "not beaten yet", over and over, position
        # never leaving the entrance.
        #
        # The lesson is the same one the gates taught and this function only
        # half-learned: escalate on the WALK FAILING, not on nav predicting it
        # will. If we are not already beside the leader, prove we can get there
        # with the savestate search before spending a talk on it.
        if cell not in self._adjacent_to(d.pos()):
            if not d.goto(*self._nearest_adjacent(cell), map_name=obj.map_name,
                          on_battle="fight"):
                self.note(f"walk to {obj.leader} failed ({d.last_goto_reason}); "
                          f"asking the game instead")
                for spot in self._adjacent_to(cell):
                    if d.reach_cell(*spot, map_name=obj.map_name,
                                    on_battle="fight"):
                        self.note(f"routed through {obj.map_name}'s floor "
                                  f"to {spot}")
                        break
        self.note(f"challenging {obj.leader} at {cell} for badge {obj.badge}")
        before = self.quest.badges()
        try:
            d.talk_to(*cell)
        except TravelInterrupted:
            raise
        except Exception as exc:  # noqa: BLE001
            self.note(f"could not reach {obj.leader}: {str(exc)[:90]}")
            d.advance_scene(20000)
            return
        d.advance_scene(60000)
        after = self.quest.badges()
        if after > before:
            # No explicit save here: Autosave.tick() already recognises a
            # badge as a milestone and gives it its own permanent filename.
            self.note(f"BADGE {after}/8 -- {obj.leader} beaten")
        else:
            self.note(f"{obj.leader} not beaten yet ({after}/8 badges)")

    @staticmethod
    def _adjacent_to(cell) -> list:
        """The four cells touching this one. A leader STANDS on their cell, so
        the reachable target is always a neighbour of it."""
        x, y = cell
        return [(x, y - 1), (x, y + 1), (x - 1, y), (x + 1, y)]

    def best_grind_map(self):
        """The reachable route with the most species we do NOT own.

        "Nearest grass" was the old rule and it is why a run spent hours on
        Route 101, which has exactly three species in its wild table. Route 102
        has eight and Petalburg Woods seven, several of them found nowhere
        else nearby -- so the same walking buys dex entries and type coverage
        instead of the same three Zigzagoon.

        Recomputed every GRIND_REVIEW seconds because routing 70 candidates is
        not free and the answer only moves when the party or the dex does.
        """
        now = time.time()
        if self._grind_map and now - self._grind_picked < GRIND_REVIEW:
            return self._grind_map
        d = self.d
        try:
            owned = {m.species for m in d.state.party() if not m.is_egg}
        except Exception:  # noqa: BLE001
            owned = set()
        wild = None
        try:
            from pokeagent import dex as dexmod

            wild = dexmod.DexTarget(
                d.emu, d.names, d.consts, d.nav, spec=d.spec
            ).wild
        except Exception as exc:  # noqa: BLE001
            log.debug("no wild table for grind choice (%s)", exc)

        here = d.map_name()
        best, best_score = None, -1
        for name in self._grass_maps():
            if wild is None:
                continue
            try:
                species = {s.species for s in wild.for_map(name)}
            except Exception:  # noqa: BLE001
                continue
            if not species:
                continue
            new_here = len(species - owned)
            # A route we are standing on wins ties, so the run does not
            # ping-pong between two equally good routes forever.
            score = new_here * 10 + (1 if name == here else 0)
            if score <= best_score:
                continue
            try:
                if name != here and d.nav.route_legs(here, d.pos(), name) is None:
                    continue
            except Exception:  # noqa: BLE001
                continue
            best, best_score = name, score
        self._grind_map = best or here
        self._grind_picked = now
        if best and best != here:
            self.note(f"better hunting on {best}; heading there")
        return self._grind_map

    def grind_step(self):
        """One step of pacing in grass. Wilds are how both parity and the dex
        get filled, so this is the default activity."""
        d = self.d
        # A ROD FIRST, when this map owes us one. Fishing slots cannot be
        # reached by walking at all -- about thirty Hoenn species live only
        # there -- so a bank we are already standing beside is worth more dex
        # than another lap of the same grass. Returns False instantly without
        # a rod, which is most of the run.
        if self.fish_for_dex():
            return
        want = self.best_grind_map()
        if want and want != d.map_name() and not self._still_given_up(
                self._travel_given_up, want):
            self.head_for(want)
            return
        grass = d.nav.find_tiles(d.map_name(), "grass")
        if not grass:
            # Nothing to do here: move toward the objective's next area.
            self.wander()
            return
        target = self.rng.choice(grass)
        d.goto(*target)
        for _ in range(6):
            if d.in_battle():
                return
            d.step_dir(self.rng.choice("UDLR"))
            self.steps += 1
            self.feed.count("steps")

    def wander(self):
        """No grass here: go to the nearest map that has some.

        Routing to a named destination rather than picking a random exit,
        because an indoor map has warps and no connections at all -- a
        connections-only wander sits in Birch's lab forever, which is exactly
        what the first run of this loop did.
        """
        d = self.d
        here = d.map_name()
        best = None
        for name in self._grass_maps():
            route = d.nav.route(here, name, max_hops=6)
            if route and (best is None or len(route) < len(best[1])):
                best = (name, route)
        if best is None:
            # Nowhere with grass within reach: take any exit rather than idle.
            for e in d.exits():
                try:
                    if e["kind"] == "warp":
                        if d.take_warp(e["x"], e["y"]):
                            return
                    else:
                        d.travel(e["dest"], on_battle="fight")
                        return
                except Exception:  # noqa: BLE001
                    continue
            d.advance_scene(4000)
            return
        name, route = best
        self.note(f"no grass on {here}; heading for {name} ({len(route) - 1} hops)")
        try:
            d.travel(name, on_battle="fight")
        except TravelInterrupted:
            raise
        except Exception as exc:  # noqa: BLE001
            log.debug("could not reach %s: %s", name, exc)
            d.advance_scene(4000)

    def _grass_maps(self):
        """Maps that really have wild land encounters.

        Gated on the ROM's own wild table, not on a grass-kind tile count. A
        tile-count test sent an earlier run into the Oldale Pokemon Centre 2F
        over and over, because a decorative metatile in there classifies as an
        encounter tile -- the classifier is right about the tile and wrong
        about the map. gWildMonHeaders is the authority on whether a map
        generates wild Pokemon at all.
        """
        if getattr(self, "_grass_cache", None) is None:
            wild = None
            try:
                from pokeagent import dex

                wild = dex.DexTarget(
                    self.d.emu, self.d.names, self.d.consts, self.d.nav,
                    spec=self.d.spec,
                ).wild
            except Exception as exc:  # noqa: BLE001
                log.debug("wild table unavailable (%s); falling back", exc)

            out = []
            for name in self.d.nav.index:
                try:
                    if not self.d.nav.find_tiles(name, "grass"):
                        continue
                    if wild is not None and not wild.for_map(name):
                        continue
                    out.append(name)
                except Exception:  # noqa: BLE001 - a few layouts are stubs
                    continue
            self._grass_cache = out
            log.info("%d maps have wild land encounters", len(out))
        return self._grass_cache

    #: How many casts to spend in one visit before going back to the loop.
    CASTS_PER_VISIT = 12

    def fish_for_dex(self) -> bool:
        """Fish, when a rod is held and this map has water species we lack.

        The rod is the only encounter METHOD the run cannot substitute: slots
        0-1 are Old, 2-4 Good, 5-9 Super (src/wild_encounter.c:200-235), and no
        amount of walking reaches them. About thirty species in Hoenn are rod
        exclusive.

        Deliberately conservative. It does nothing at all without a rod, so it
        stays dormant until the chain collects one, and it spends a bounded
        number of casts per visit so a bad spot cannot own the loop. The catch
        itself needs no special handling -- whatever bites is a wild battle,
        and the standing policy already throws a ball at anything new.
        """
        d = self.d
        rods = [k for k in (d.state.bag().get("key_items") or {}) if "ROD" in k]
        if not rods:
            return False
        here = d.map_name()
        want = self._unfished_species(here)
        if not want:
            return False
        spot = self._water_edge()
        if spot is None:
            self.note(f"{here} has rod species we lack but no reachable bank")
            return False
        cell, face = spot
        if d.pos() != cell and not d.goto(*cell, on_battle="fight"):
            return False
        if d.facing() != face:
            d.step_dir(face)          # a refused step still turns the player
        hooked = 0
        for _ in range(self.CASTS_PER_VISIT):
            if not d.fish():
                if d.last_fish_reason == "no-rod":
                    return False
                if d.last_fish_reason == "wrong-tile":
                    # A BATTLE MOVES YOU. The first catch of the session ended
                    # with the player facing away from the water and the whole
                    # visit was abandoned on the next cast -- one species per
                    # trip instead of a session. Re-take the bank and carry on;
                    # give up only when the bank itself is gone.
                    spot = self._water_edge()
                    if spot is None:
                        self.note("the bank is gone; stopping fishing")
                        return hooked > 0
                    cell, face = spot
                    if d.pos() != cell and not d.goto(*cell, on_battle="fight"):
                        return hooked > 0
                    if d.facing() != face:
                        d.step_dir(face)
                    continue
                continue
            hooked += 1
            self.fight()
        self.note(f"fished {here}: {hooked} bite(s) from {len(want)} wanted")
        return True

    def _rod_kinds(self) -> frozenset:
        """Encounter kinds the rods in the bag can actually roll.

        `WildSlot.kind` already carries the rod -- `old_rod`, `good_rod`,
        `super_rod` -- so the held rod maps straight onto it. (My first
        attempt filtered on a `method` attribute that does not exist, and the
        second re-derived slot ranges from wild_encounter.c when the data was
        already split. Read the data before modelling it.)

        A rod only reaches its OWN kind, so wanting a Super-only species while
        holding an Old rod must not send the run to a bank.
        """
        keys = set(self.d.state.bag().get("key_items") or {})

        def held(name):
            return any(name in k.replace(" ", "_").upper() for k in keys)

        kinds = set()
        if held("OLD_ROD"):
            kinds.add("old_rod")
        if held("GOOD_ROD"):
            kinds.add("good_rod")
        if held("SUPER_ROD"):
            kinds.add("super_rod")
        return frozenset(kinds)

    def _unfished_species(self, map_name) -> set:
        """Species this map yields to a ROD that the dex has not recorded."""
        try:
            from pokeagent import dex as dexmod

            target = self._dex_target()
            if target is None:
                return set()
            caught, _seen = target.dex_flags(self.d.state)
            # Only what the rod in the bag can roll. `WildSlot.kind` is
            # already per-rod (`old_rod`/`good_rod`/`super_rod`), matching the
            # engine's own slot split (ChooseWildMonIndex_Fishing,
            # src/wild_encounter.c:200-235).
            reach = self._rod_kinds()
            out = set()
            for slot in target.wild.for_map(map_name):
                if getattr(slot, "kind", "") not in reach:
                    continue
                nat = target.evolutions.natdex(slot.species)
                if nat and nat not in caught:
                    out.add(slot.species)
            return out
        except Exception as err:  # noqa: BLE001
            log.debug("no rod table for %s: %s", map_name, err)
            return set()

    def _dex_target(self):
        if getattr(self, "_dexmod_target", None) is None:
            try:
                from pokeagent import dex as dexmod

                self._dexmod_target = dexmod.DexTarget(
                    self.d.emu, self.d.names, self.d.consts, self.d.nav,
                    spec=self.d.spec,
                )
            except Exception as err:  # noqa: BLE001
                log.debug("no dex target: %s", err)
                self._dexmod_target = False
        return self._dexmod_target or None

    def _water_edge(self):
        """A reachable land cell beside water, and the direction to face it."""
        d = self.d
        here = d.map_name()
        try:
            reach = d.nav.reachable(here, d.pos(), d.elevation())
        except Exception:  # noqa: BLE001
            return None
        best = None
        for (x, y) in sorted(reach):
            for mv, (dx, dy) in (("U", (0, -1)), ("D", (0, 1)),
                                 ("L", (-1, 0)), ("R", (1, 0))):
                c = d.nav.cell(here, x + dx, y + dy)
                if c is None or not d.nav._is_water(c) or c.collision:
                    continue
                dist = abs(x - d.pos()[0]) + abs(y - d.pos()[1])
                if best is None or dist < best[0]:
                    best = (dist, (x, y), mv)
        return (best[1], best[2]) if best else None

    def play_the_intro(self) -> bool:
        """Drive the opening until a party exists. True while it is still busy.

        Rate-limited: a failing intro must not spin. Each attempt gets one
        pass, and the reason is journalled once rather than every step.
        """
        now = time.time()
        if now - getattr(self, "_last_intro_try", 0.0) < 20.0:
            return True
        self._last_intro_try = now
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from to_starter import drive_intro
        except Exception as err:  # noqa: BLE001
            self.note(f"cannot load the intro driver: {err}")
            return False
        ok = drive_intro(self.d)
        party = len(self.d.state.party())
        if ok and party:
            self.note(f"intro finished -- party of {party}, the run can start")
            return False
        if self._last_intro_note != self.d.map_name():
            self._last_intro_note = self.d.map_name()
            self.note(f"still playing the opening, at {self.d.map_name()}")
        return True

    def standing_policy(self):
        """The policy EVERY battle gets, not just the ones this loop starts.

        `goto`, `travel` and `_cross_seam` fight their own interruptions by
        calling `Driver.fight()` with no policy, and that is where almost every
        wild encounter in a run actually happens. So the catch decision used to
        apply only while deliberately grinding, and everything met on the road
        was knocked out -- a CARVANHA and a GOLDEEN, neither of them registered
        caught, inside the same minute.

        Rebuilt per turn on purpose: both halves depend on live state. The
        training policy reads the party's levels and the catch plan reads the
        dex and the ball pocket, and a ball that lands changes both.
        """

        def decide(frame):
            base = self.team.training_policy(
                tolerance=PARITY_BAND, safe_hp_frac=self.safe_lead_hp
            )
            try:
                plan = self.catcher.plan(frame)
            except Exception as err:  # noqa: BLE001 - never lose a battle here
                log.debug("standing catch plan failed: %s", err)
                plan = None
            if plan:
                if plan.reason != self._last_catch_note:
                    self._last_catch_note = plan.reason
                    self.note(f"going for a catch -- {plan.reason}")
                return self.catcher.policy(plan, inner=base)(frame)
            return base(frame)

        return decide

    def fight(self):
        d = self.d
        for _ in range(80):
            if d.state.battle_ready():
                break
            d.emu.tick(20)
        if not d.in_battle():
            return
        battle = d.state.battle()
        foe = battle.mons[1] if len(battle.mons) > 1 else {}
        # Train the laggards: they must be the sole participant and land the
        # KO, because Gen 3 splits experience.
        policy = self.team.training_policy(
            tolerance=PARITY_BAND, safe_hp_frac=self.safe_lead_hp
        )

        # ...unless this one is worth keeping. A run that only ever KOs wilds
        # never builds a team: 604 battles in, the party was still one mon with
        # nine uncovered types. The catch decision wraps the training policy
        # rather than replacing it, so the move that WEAKENS is still chosen by
        # the same maths.
        before_party = len(d.state.party())
        plan = self.catcher.plan(d.battle_frame())
        if plan:
            self.note(f"going for a catch -- {plan.reason}")
            policy = self.catcher.policy(plan, inner=policy)

        result = d.fight(policy=policy)
        self.battles += 1
        after_party = len(d.state.party())
        if after_party > before_party:
            self.catcher.caught += 1
            self.feed.count("caught")
            caught = d.state.party()[-1]
            self.note(
                f"CAUGHT {caught.nickname} L{caught.level} "
                f"-- party is now {after_party}"
            )
        if result.get("outcome") == "B_OUTCOME_WON":
            self.feed.count("battles_won")
        elif result.get("outcome") == "B_OUTCOME_LOST":
            # Remember WHO beat us and how strong we were. Re-running a gym we
            # cannot win halves the money every time, and money is what buys
            # the balls the Pokedex needs -- this run drained to 62 that way.
            try:
                lost_to = self.quest.next_objective().leader
                if lost_to:
                    self.quest.note_loss(lost_to)
                    self.note(f"lost to {lost_to} at party total "
                              f"{self.quest.party_total()}; "
                              "training before a rematch")
            except Exception:  # noqa: BLE001 - never lose the run to bookkeeping
                pass
        self.note(
            f"battle {self.battles}: vs {foe.get('name','?')} "
            f"L{foe.get('level','?')} -> {result.get('outcome')} "
            f"in {len(result.get('turns', []))} turns"
        )
        d.advance_scene(20000)

    def finish(self):
        d = self.d
        obj = self.objective.current()
        self.note(f"stopping: {obj.name} at {obj.percent:.0f}%")
        path = self.autosave.checkpoint("final")
        log.info("final checkpoint %s", path)
        log.info("autosave: %s", self.autosave.stats())
        log.info("metrics: %s", self.metrics.summary())
        log.info("projection: %s", self.metrics.projection())
        log.info("catching: %s", self.catcher.stats())
        if self.brain:
            log.info("brain: %s", self.brain.stats())
            log.info("small choices: %s", d.choices.stats())
        self.feed.detach()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="saves/lab.state")
    ap.add_argument("--game", default="sapphire")
    ap.add_argument("--minutes", type=float, default=5.0,
                    help="0 or less runs until stopped")
    ap.add_argument("--feed", default="default")
    ap.add_argument("--session", default="play")
    ap.add_argument("--no-brain", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO, format="%(message)s"
    )
    Session(
        a.state, a.minutes, game=a.game, use_brain=not a.no_brain,
        feed_name=a.feed, session=a.session,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
