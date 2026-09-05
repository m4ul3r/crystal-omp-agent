// Parsing and formatting for the pokeagent live feed. Everything in here
// is a pure function of the bytes the publisher wrote, so what the widget shows
// is auditable by reading the feed files by hand -- and so nothing in the bar
// has to guess when the feed says something unexpected.
//
// Feed format (pokeagent/live.py):
//   <feed>.json   one snapshot object, rewritten atomically at state_hz
//   <feed>.jsonl  one narration object per line, appended
//   <feed>.png    the current framebuffer, rewritten atomically at fps
//
// Optional keys, and why every one of them is optional
// ---------------------------------------------------
// The harness is being generalised to drive any Gen 1-3 game, and each
// generation's adapter can answer a different subset of questions: a Gen 1
// adapter has no notion of a Hoenn dex, an intro screen has no party, an older
// publisher predates half of these keys. So `objective`, `dex`, `team`,
// `enemy`, `game` and `counters` are all optional, and the rule throughout this
// file is that a missing key yields null, and null makes the section that would
// have shown it disappear entirely.
//
// Never a "0", never a "-", never a labelled row with nothing beside it. A
// widget reporting 0% dex completion because the publisher said nothing about
// the dex is not a cosmetic problem: it is the widget lying about the run.

// A snapshot older than this many seconds means the driver is gone (crashed,
// killed, or paused in a debugger) even though `live` is still true. The
// publisher writes at state_hz = 4, so 6s is ~24 missed writes: slow enough to
// survive a stutter, fast enough that a dead run stops claiming to be alive.
var DEFAULT_STALE_AFTER = 6

function parseState(raw) {
  try {
    var data = JSON.parse(String(raw || ""))
    return (data && typeof data === "object") ? data : null
  } catch (e) {
    // Half-written JSON cannot reach us -- live.py renames into place -- so a
    // parse failure means a truncated or foreign file. Report nothing rather
    // than a partial snapshot; the caller falls back to the idle state.
    return null
  }
}

// Seconds since the publisher wrote this snapshot. `t` is the publisher's wall
// clock, not the file mtime: the widget may be reading over a filesystem whose
// timestamps it does not trust, and the publisher is the authority on when it
// last saw the game.
function ageSeconds(state, nowMs) {
  if (!state || typeof state.t !== "number") return Infinity
  return Math.max(0, nowMs / 1000 - state.t)
}

function isRunning(state, nowMs, staleAfter, wasRunning) {
  if (!state) return false
  if (state.live !== true) return false
  var limit = staleAfter > 0 ? staleAfter : DEFAULT_STALE_AFTER
  var age = ageSeconds(state, nowMs)
  // HYSTERESIS, because this drove a reported flicker. `running` is polled
  // every 400ms and a SINGLE threshold makes it chatter whenever the
  // publisher's cadence sits near the limit -- and the driver stalls the
  // publisher for seconds at a time on its own (a battle, a PC menu, route
  // planning, the gap between two runs). Every flip changes the stale badge's
  // opacity and starts/stops the animated mark, so the panel blinks.
  //
  // Two thresholds instead: it takes a genuinely longer silence to declare
  // the run stopped than it takes to call it started again.
  var exitLimit = limit * 2
  return wasRunning ? age <= exitLimit : age <= limit
}

// Newest `count` narration lines, oldest first. Only the tail of the file is
// scanned: the log rotates at 4000 lines and the panel shows a handful.
function tailNotes(raw, count) {
  var lines = String(raw || "").split("\n")
  var out = []
  for (var i = lines.length - 1; i >= 0 && out.length < count; i--) {
    var line = lines[i].replace(/^\s+|\s+$/g, "")
    if (line === "") continue
    try {
      var note = JSON.parse(line)
      if (note && typeof note === "object" && note.msg) out.unshift(note)
    } catch (e) {
      // A line still being appended when we read: skip it, not the file.
    }
  }
  return out
}

function latestNote(notes) {
  return (notes && notes.length > 0) ? notes[notes.length - 1] : null
}

// ------------------------------------------------------------- optional keys

// A nested object, or null. Arrays are rejected on purpose: `dex: []` is a
// publisher bug, and treating it as an object would silently read undefined out
// of it forever.
function group(state, key) {
  if (!state) return null
  var value = state[key]
  if (!value || typeof value !== "object" || Array.isArray(value)) return null
  return value
}

// A finite number, or null. `null`, `undefined`, "" and NaN all mean "not
// reported"; 0 does not, and has to survive.
function num(value) {
  if (value === null || value === undefined || value === "") return null
  var n = Number(value)
  return isFinite(n) ? n : null
}

function str(value) {
  if (value === null || value === undefined) return ""
  return String(value).replace(/^\s+|\s+$/g, "")
}

// Percentages arrive 0-100 by contract with the publisher. Clamped, because a
// progress bar wider than its own track is a rendering bug rather than good
// news, and left unrounded so the bar keeps its precision -- only the printed
// label rounds.
function percent(value) {
  var n = num(value)
  if (n === null) return null
  return Math.max(0, Math.min(100, n))
}

function percentLabel(value) {
  return value === null ? "" : Math.round(value) + "%"
}

function fraction(value) {
  return value === null ? 0 : value / 100
}

// Thousands separators, done by hand because QML's JS engine gives no locale
// the shell can rely on.
function grouped(n) {
  var negative = n < 0
  var digits = String(Math.abs(Math.round(n))).split("").reverse()
  var out = []
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && i % 3 === 0) out.push(",")
    out.push(digits[i])
  }
  return (negative ? "-" : "") + out.reverse().join("")
}

// ------------------------------------------------------------------ party

function party(state) {
  return (state && Array.isArray(state.party)) ? state.party : []
}

// The lead is the first mon that can actually fight: an egg reads level 0 and
// 0 HP, and showing "EGG 0/0" as the run's lead is how you end up with an HP
// bar that is permanently empty.
function lead(state) {
  var list = party(state)
  for (var i = 0; i < list.length; i++) {
    if (!list[i].egg) return list[i]
  }
  return list.length > 0 ? list[0] : null
}

function hpFraction(mon) {
  if (!mon || !mon.max_hp) return 0
  return Math.max(0, Math.min(1, mon.hp / mon.max_hp))
}

// Green above half, amber in the danger band, red in the critical band -- the
// same three-colour rule the games themselves use for the HP bar.
function hpColor(filled) {
  if (filled > 0.5) return "#6fbf73"
  if (filled > 0.2) return "#d8b34a"
  return "#c25555"
}

function monName(mon) {
  if (!mon) return ""
  var nick = String(mon.nickname || "").replace(/^\s+|\s+$/g, "")
  return nick !== "" ? nick : String(mon.species || "?")
}

// An egg has no level, no HP and no species the player is allowed to know, so
// it gets neither an HP readout nor a stat line -- the game shows the same
// nothing, and "EGG  L0  0/0" reads like a bug.
function monHp(mon) {
  if (!mon || mon.egg) return ""
  if (mon.max_hp === null || mon.max_hp === undefined) return ""
  return mon.hp + "/" + mon.max_hp
}

function monLine(mon) {
  if (!mon) return ""
  if (mon.egg) return "EGG"
  var bits = [monName(mon)]
  if (mon.level) bits.push("L" + mon.level)
  if (mon.status) bits.push(String(mon.status).toUpperCase())
  return bits.join("  ")
}

// ------------------------------------------------------------------- the game

// Which of Gen 1-3 is booted. The widget never names a cartridge itself: the
// publisher knows which ROM it opened and the widget would be wrong the moment
// the user picked a different one.
function game(state) {
  var g = group(state, "game")
  if (!g) return null
  var out = {
    id: str(g.id),
    name: str(g.name),
    generation: num(g.generation),
    region: str(g.region)
  }
  if (out.id === "" && out.name === "" && out.generation === null && out.region === "")
    return null
  return out
}

// The popup header's title. With no `game` key the honest answer is the series
// rather than a guess at the cartridge.
function gameTitle(state) {
  var g = game(state)
  if (!g) return "Pokemon"
  if (g.name !== "") return g.name
  if (g.id !== "") return g.id
  return "Pokemon"
}

// "Gen 3 - Hoenn". PanelHero uppercases what it is given, so the casing here is
// for the notification body, which quotes the same string.
function gameTag(state) {
  var g = game(state)
  if (!g) return ""
  var bits = []
  if (g.generation !== null) bits.push("Gen " + Math.round(g.generation))
  if (g.region !== "") bits.push(g.region)
  return bits.join(" \u00b7 ")
}

// The header's second line. The game identity is what belongs there, but the
// header must never be blank, so where the player is standing is the fallback
// -- and it is all an older feed can offer. A run that has stopped says how
// long ago, in the same line: it used to be a second line of its own above
// the frame, reserving its height for the whole session.
function headerMeta(state, running, present, ageSec) {
  if (!present) return "No feed"
  if (!running) {
    var what = state && state.live === false ? "Run ended" : "Feed stale"
    return ageSec === undefined || !isFinite(ageSec) ? what : what + " \u00b7 " + agoLabel(ageSec)
  }
  var tag = gameTag(state)
  return tag !== "" ? tag : positionLabel(state)
}

// "12s ago", "4 min ago", "2 h ago": the resolution a watcher glancing at a
// dead feed actually wants, not a five-digit second count.
function agoLabel(sec) {
  var s = Math.max(0, Math.round(sec))
  if (s < 60) return s + "s ago"
  var m = Math.round(s / 60)
  if (m < 60) return m + " min ago"
  var h = Math.round(m / 60)
  if (h < 48) return h + " h ago"
  return Math.round(h / 24) + " d ago"
}

// ------------------------------------------------------------- the objective

function objective(state) {
  var o = group(state, "objective")
  if (!o) return null
  var out = { name: objectiveTitle(o.name), detail: str(o.detail), percent: percent(o.percent) }
  if (out.name === "" && out.detail === "" && out.percent === null) return null
  return out
}

// The publisher names objectives by their rank in the ladder -- "1. Complete
// the game" -- and the ladder is drawn in full a few sections down, so the
// number says nothing here the STAGES list does not say better. The heading
// carries its own weight.
function objectiveTitle(name) {
  return str(name).replace(/^\d+\.\s+/, "")
}

// --------------------------------------------------------------------- dex

// `percent` is the publisher's if it sent one and derived from the counts if it
// did not: the arithmetic belongs in one place, not in a QML binding. A dex with
// no achievable total cannot have a percentage -- 60 of the 188 solo-achievable
// Hoenn entries are evolutions with no catch location, so "caught / total dex
// size" would be a different and wronger number.
function dex(state) {
  var d = group(state, "dex")
  if (!d) return null
  var out = {
    caught: num(d.caught),
    achievable: num(d.achievable),
    percent: percent(d.percent)
  }
  if (out.percent === null && out.caught !== null && out.achievable > 0)
    out.percent = percent(out.caught / out.achievable * 100)
  if (out.caught === null && out.percent === null) return null
  return out
}

function dexLabel(d) {
  if (!d || d.caught === null) return ""
  if (d.achievable !== null && d.achievable > 0)
    return d.caught + "/" + d.achievable
  return String(d.caught)
}

// "142/188  76%": the count and the percentage read as one fact, so they share
// one caption beside one bar rather than competing for two rows.
function dexCaption(d) {
  var bits = []
  var counted = dexLabel(d)
  if (counted !== "") bits.push(counted)
  var pct = percentLabel(d ? d.percent : null)
  if (pct !== "") bits.push(pct)
  return bits.join("  ")
}

// ------------------------------------------------------------------ stages

// The goal ladder. Absent for a feed that predates it, or for a game whose
// adapter does not compute one, so the section hides rather than showing an
// empty plan.
function stages(state) {
  var rows = state && state.stages
  if (!rows || !rows.length) return []
  var out = []
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i]
    if (!r || typeof r.name !== "string") continue
    out.push({
      "name": r.name,
      "rank": typeof r.rank === "number" ? r.rank : 0,
      "percent": typeof r.percent === "number" ? r.percent : null,
      "detail": typeof r.detail === "string" ? r.detail : "",
      "current": r.current === true,
      "complete": r.done === true
    })
  }
  return out
}

// What sits at the right edge of a stage row. A finished stage says so; a
// percentage on a completed rung reads as if there were something left in it.
function stageValue(s) {
  if (!s) return ""
  if (s.complete) return "done"
  return percentLabel(s.percent)
}

// ------------------------------------------------------------------ sprites

// The decomp names its sprite directories in lowercase alphanumerics while the
// ROM spells species for a Game Boy screen ("MR. MIME", "FARFETCH'D",
// "NIDORAN\u2640"), so the slug is derived the same way make_sprites.py derives
// the filename. Gendered Nidoran are special-cased for the same reason there:
// stripping the symbol would collide the two.
function spriteSlug(species) {
  if (typeof species !== "string" || species === "") return ""
  var raw = species.toLowerCase()
  if (raw.indexOf("nidoran") !== -1)
    return species.indexOf("\u2640") !== -1 ? "nidoran_f" : "nidoran_m"
  var out = ""
  for (var i = 0; i < raw.length; i++) {
    var c = raw.charAt(i)
    if ((c >= "a" && c <= "z") || (c >= "0" && c <= "9")) out += c
  }
  return out
}

// Relative to the plugin directory, which is where install.sh puts sprites/.
// An egg has no species to draw and returns "", which the Image treats as
// nothing to show rather than as a broken source.
function spriteFor(mon) {
  if (!mon || mon.egg === true) return ""
  var slug = spriteSlug(mon.species)
  return slug === "" ? "" : "sprites/" + slug + ".png"
}

// -------------------------------------------------------------------- team

function team(state) {
  var t = group(state, "team")
  if (!t) return null
  var gaps = []
  if (Array.isArray(t.coverage_gaps)) {
    for (var i = 0; i < t.coverage_gaps.length; i++) {
      var gap = str(t.coverage_gaps[i])
      if (gap !== "") gaps.push(gap)
    }
  }
  var out = {
    minLevel: num(t.min_level),
    maxLevel: num(t.max_level),
    spread: num(t.spread),
    gaps: gaps,
    // An empty coverage_gaps array is a real answer -- "nothing is missing" --
    // and has to be distinguishable from the key being absent, or a team with
    // perfect coverage looks identical to a publisher that cannot compute it.
    gapsReported: Array.isArray(t.coverage_gaps)
  }
  if (out.minLevel === null && out.maxLevel === null && out.spread === null
      && !out.gapsReported)
    return null
  // A publisher with no party to measure still reports an empty gaps array,
  // and "GAPS none" under an empty team claims a coverage it does not have.
  // No mons, no numbers: nothing to say yet.
  if (out.minLevel === null && out.maxLevel === null && out.spread === null
      && party(state).length === 0)
    return null
  return out
}

function levelLabel(t) {
  if (!t) return ""
  if (t.minLevel === null && t.maxLevel === null) return ""
  if (t.minLevel === null) return "L" + t.maxLevel
  if (t.maxLevel === null) return "L" + t.minLevel
  if (t.minLevel === t.maxLevel) return "L" + t.minLevel
  return "L" + t.minLevel + "-" + t.maxLevel
}

// English pluralises zero, so only a spread of exactly one level is singular.
function spreadLabel(t) {
  if (!t || t.spread === null) return ""
  return Math.abs(t.spread) === 1 ? "level" : "levels"
}

// "nothing missing" is worth saying out loud: it is the state the team-building
// policy is aiming at, and a blank row would read as "not computed".
function coverageLabel(t) {
  if (!t || !t.gapsReported) return ""
  if (t.gaps.length === 0) return "none"
  return t.gaps.join(", ")
}

// The team's numbers as grid cells. Coverage gaps are deliberately not here:
// the list is as long as the number of types the team cannot hit, and squeezing
// it into a half-width cell would elide away the very information it carries.
function teamCells(state) {
  var t = team(state)
  var out = []
  if (!t) return out
  var levels = levelLabel(t)
  if (levels !== "") out.push({ label: "LEVELS", value: levels })
  if (t.spread !== null)
    out.push({ label: "SPREAD", value: t.spread + " " + spreadLabel(t) })
  return out
}

// ------------------------------------------------------------------- battle

function enemy(state) {
  var e = group(state, "enemy")
  if (!e) return null
  var out = {
    species: str(e.species),
    level: num(e.level),
    hp: num(e.hp),
    maxHp: num(e.max_hp)
  }
  if (out.species === "" && out.level === null && out.hp === null) return null
  return out
}

function enemyLine(e) {
  if (!e) return ""
  var bits = [e.species !== "" ? e.species : "?"]
  if (e.level !== null) bits.push("L" + e.level)
  return bits.join("  ")
}

function enemyHp(e) {
  if (!e || e.hp === null || e.maxHp === null) return ""
  return e.hp + "/" + e.maxHp
}

function enemyFraction(e) {
  if (!e || e.hp === null || !e.maxHp) return 0
  return Math.max(0, Math.min(1, e.hp / e.maxHp))
}

// ------------------------------------------------------------------ labels

function money(state) {
  var n = num(state && state.money)
  return n === null ? "" : "$" + grouped(n)
}

// Map names arrive as the decomp's own constants, which differ by generation:
// pret's Gen 3 maps are CamelCase run together ("LittlerootTown", "Route101",
// "SeafloorCavern_Room1") and the Gen 1-2 decomps shout in snake case
// ("VIOLET_CITY"). Split on the underscore, then on the two transitions that
// are always a word boundary -- lowercase to uppercase, and lowercase to digit
// -- so the panel reads like the game does instead of like a header file.
//
// Both left-hand classes are deliberately lowercase-only. The floor suffixes
// are the counterexamples that would otherwise be mangled: "MtPyre_1F" must not
// become "Mt Pyre 1 F", and "AbandonedShip_Rooms_B1F" must not become
// "... B 1F". Digit-to-uppercase and uppercase-to-digit only ever occur inside
// those suffixes in Gen 1-3, so leaving them alone is exactly right.
function mapLabel(state) {
  var name = str(state && state.map)
  if (name === "") return ""
  return name
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/([a-z])([0-9])/g, "$1 $2")
    .replace(/\s+/g, " ")
}

function positionLabel(state) {
  var where = mapLabel(state)
  if (!state || !state.pos) return where
  var x = num(state.pos.x)
  var y = num(state.pos.y)
  if (x === null || y === null) return where
  return where === "" ? "(" + x + "," + y + ")" : where + " (" + x + "," + y + ")"
}

// The publisher writes the facing as the game stores it: one of the four
// DIR_* values, abbreviated. Anything else is passed through rather than
// dropped, because a new adapter inventing "NE" should show up as "NE" and not
// as silence.
var FACINGS = { "U": "up", "D": "down", "L": "left", "R": "right" }

function facingLabel(state) {
  if (!state || !state.pos) return ""
  var f = str(state.pos.facing)
  if (f === "") return ""
  var word = FACINGS[f.toUpperCase()]
  return word ? word : f.toLowerCase()
}

// "(2,2) · facing down": where on the map, as one short phrase for the HUD's
// right edge. Either half is optional.
function whereLabel(state) {
  var bits = []
  if (state && state.pos) {
    var x = num(state.pos.x)
    var y = num(state.pos.y)
    if (x !== null && y !== null) bits.push("(" + x + "," + y + ")")
  }
  var facing = facingLabel(state)
  if (facing !== "") bits.push("facing " + facing)
  return bits.join(" \u00b7 ")
}

// Every Gen 1-3 game ships exactly eight gyms, so the denominator is a fact
// about the medium rather than per-game data to be read out of a ROM. Null
// when the publisher does not report badges at all.
var BADGE_SLOTS = 8

function badgeCount(state) {
  return num(state && state.badges)
}

// The HUD's small facts, as label/value cells in a fixed order. Built as a
// list so an absent key leaves no hole rather than a label with nothing
// beside it.
function hudCells(state) {
  var out = []
  if (!state) return out
  var cash = money(state)
  if (cash !== "") out.push({ label: "MONEY", value: cash })
  var play = str(state.play_time)
  if (play !== "") out.push({ label: "PLAY", value: play })
  var frame = num(state.frame)
  if (frame !== null) out.push({ label: "FRAME", value: grouped(frame) })
  return out
}

// Six party slots, in order, empty ones as null: the tiles draw every slot
// so the party reads as the game's own six-slot screen, and a run with two
// mons shows four open places rather than a shorter row.
var PARTY_SLOTS = 6

function partySlots(state) {
  var list = party(state)
  var out = []
  for (var i = 0; i < PARTY_SLOTS; i++) out.push(i < list.length ? list[i] : null)
  return out
}

function monLevel(mon) {
  if (!mon || mon.egg) return ""
  var lvl = num(mon.level)
  return lvl === null ? "" : "L" + lvl
}

// Session counters, in a fixed order so the grid does not reshuffle itself
// between polls. FRAMES is the count of frames this session advanced, which is
// a different number from the run grid's FRAME (the emulator's frame counter).
var COUNTERS = [
  ["battles_won", "WON"],
  ["caught", "CAUGHT"],
  ["faints", "FAINTS"],
  ["saves", "SAVES"],
  ["steps", "STEPS"],
  ["frames", "FRAMES"]
]

function counterCells(state) {
  var counters = group(state, "counters")
  var out = []
  if (!counters) return out
  for (var i = 0; i < COUNTERS.length; i++) {
    var n = num(counters[COUNTERS[i][0]])
    if (n === null) continue
    out.push({ label: COUNTERS[i][1], value: grouped(n) })
  }
  return out
}

// What the bar shows. The lead's HP is the one number that changes minute to
// minute and the one a watcher actually reacts to, so it wins the slot; a run
// with no party yet (every one of these games opens with a long intro) falls
// back to where it is.
function barLabel(state, running) {
  if (!running) return ""
  var mon = lead(state)
  if (mon && mon.max_hp) return monName(mon) + " " + monHp(mon)
  var where = mapLabel(state)
  return where.length > 18 ? where.substring(0, 17) + "\u2026" : where
}

// The bar tooltip and the notification body: the publisher already computes a
// status line, and repeating its own words keeps the widget from inventing a
// second vocabulary for the same state.
// The hover text. This is the ONLY thing most people will ever read, so it
// answers what a person actually wants to know -- where the run is, what it is
// trying to do, how the team is holding up -- rather than the debug status
// line it used to show, which was `frame=41083574 map=Route111 pos=(12,68)
// lead=LOTTAD L30 70/93 money=14488 badges=3/8`. Every one of those facts is
// available in the popup; none of them is a sentence.
function tooltip(state, running, configured, feedName) {
  var title = gameTitle(state)
  if (!configured) return title + "\nNo feed directory configured \u2014 open the settings to point at a ROM"
  if (!state) return title + "\nWaiting for a run on feed \u201c" + feedName + "\u201d"
  if (state.error) return title + "\n" + state.error

  var lines = []

  // Line 1: who and where. The trainer name makes it feel like a save file
  // rather than a process. `player` is a bare string in the feed, not an
  // object -- reading `.name` off it silently produced the game title for
  // every run.
  var who = str(state.player) || title
  var place = prettyMap(state)
  lines.push(place ? who + " \u2014 " + place : who)

  // Line 2: what it is doing right now. A battle outranks the objective,
  // because that is what is on screen.
  if (state.in_battle) {
    var foe = state.opponent && state.opponent.species
    lines.push(foe ? "In battle with " + titleCase(foe) : "In battle")
  } else if (state.objective) {
    // `next_step` is the specific thing being worked on ("badge 4"); `detail`
    // is the arc ("3/8 badges, then the Elite Four"), which duplicates the
    // progress line below almost word for word.
    var step = str(state.objective.next_step)
    var obj = str(state.objective.detail)
    if (step !== "") lines.push("Working on " + step)
    else if (obj !== "") lines.push(sentence(obj))
  }

  // Line 3: the lead, since that is the mon taking the damage.
  var lead = (state.party || [])[0]
  if (lead) {
    var hp = (lead.hp !== undefined && lead.max_hp)
      ? "  " + lead.hp + "/" + lead.max_hp + " HP" : ""
    lines.push(titleCase(lead.nickname || lead.species) + "  L" + lead.level + hp)
  }

  // Line 4: the long arc. Badges, dex, and how long this has taken.
  var progress = []
  if (state.badges !== undefined && state.badges !== null)
    progress.push(state.badges + "/8 badges")
  if (state.dex && state.dex.caught !== undefined) {
    // Against what is ACHIEVABLE in this cartridge, not 386. Seven Ruby
    // exclusives and seven trade evolutions are not a shortfall.
    var goal = state.dex.achievable || state.dex.seen
    progress.push(goal ? state.dex.caught + "/" + goal + " dex"
                       : state.dex.caught + " caught")
  }
  var play = str(state.play_time)
  if (play) progress.push(play.split(":")[0] + "h")
  if (progress.length) lines.push(progress.join("  \u00b7  "))

  // Line 5: who is driving. A game can be driven by the play loop, a
  // serve.py client or a bare kernel, and which one matters when the run
  // does something odd.
  var driver = agentLine(state)
  if (driver !== "") lines.push(driver)

  // Only say something is wrong when it is.
  if (!running)
    lines.push(state.live === false ? "\u2014 run ended" : "\u2014 feed stale")

  return lines.join("\n")
}

// ------------------------------------------------------------------- agent
//
// Who is running the game. The feed always names the process (script, pid,
// host, when it attached); the play loop adds its session name, the risk it
// runs at and the local model it consults -- with whether that model is
// actually answering, since a model name on a card means nothing if every
// decision has been falling back to the maths.

function agent(state) {
  var a = group(state, "agent")
  if (!a) return null
  var out = {
    name: str(a.name),
    session: str(a.session),
    pid: num(a.pid),
    host: str(a.host),
    started: num(a.started),
    model: str(a.model),
    modelState: str(a.model_state),
    modelReason: str(a.model_reason),
    risk: num(a.risk),
    riskLabel: str(a.risk_label),
    decisions: (a.decisions && typeof a.decisions === "object") ? a.decisions : null
  }
  if (out.name === "" && out.session === "" && out.model === "" && out.risk === null)
    return null
  return out
}

// "4 h 05 m" of run, measured against the publisher's OWN clock (`t`), so a
// run that has ended stops counting instead of ageing forever.
function uptimeLabel(state, a) {
  if (!a || a.started === null || !state || typeof state.t !== "number") return ""
  var s = Math.max(0, Math.round(state.t - a.started))
  var h = Math.floor(s / 3600)
  var m = Math.floor((s % 3600) / 60)
  if (h > 0) return h + " h " + (m < 10 ? "0" : "") + m + " m"
  if (m > 0) return m + " min"
  return s + " s"
}

// "play.py · live": the script and its session. Uptime is its own cell.
function agentDriver(a) {
  if (!a) return ""
  var bits = []
  if (a.name !== "") bits.push(a.name)
  if (a.session !== "") bits.push(a.session)
  return bits.join(" \u00b7 ")
}

// The brain cell's label carries the STATE -- "BRAIN · UNREACHABLE" -- and
// its value the model name. The state is the part worth reading: it says
// whether the model is in the loop right now, and a label can say it in the
// same small caps the rest of the card uses without crowding the name.
function agentModelLabel(a) {
  if (!a || a.modelState === "") return "BRAIN"
  return "BRAIN \u00b7 " + a.modelState.toUpperCase()
}

function agentModel(a) {
  if (!a) return ""
  if (a.model !== "") return a.model
  return a.modelState === "off" ? "none" : ""
}

// "0.35 balanced"
function agentRisk(a) {
  if (!a || a.risk === null) return ""
  var n = Math.round(a.risk * 100) / 100
  return a.riskLabel !== "" ? n + " " + a.riskLabel : String(n)
}

// One sentence for the tooltip: "Driven by play.py (live), gemma4:e4b
// unreachable, risk 0.35 balanced".
function agentLine(state) {
  var a = agent(state)
  if (!a) return ""
  var bits = []
  if (a.name !== "")
    bits.push("Driven by " + a.name + (a.session !== "" ? " (" + a.session + ")" : ""))
  if (a.model !== "")
    bits.push(a.modelState !== "" ? a.model + " " + a.modelState : a.model)
  var risk = agentRisk(a)
  if (risk !== "") bits.push("risk " + risk)
  return bits.join(", ")
}

// "Route111" and "MauvilleCity_Gym_1F" are map CONSTANTS, not English. Split
// them into words so the hover text reads like a place.
function prettyMap(state) {
  var name = str(state && state.map)
  if (name === "") return ""
  return name
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/([A-Za-z])(\d)/g, "$1 $2")
    .replace(/\s+/g, " ")
}

function titleCase(text) {
  var t = str(text)
  return t === "" ? "" : t.charAt(0) + t.slice(1).toLowerCase()
}

function sentence(text) {
  var t = str(text)
  return t === "" ? "" : t.charAt(0).toUpperCase() + t.slice(1)
}

// A message buffer holds whatever the last script wrote and keeps holding it
// after the box closes, so it is shown as context rather than as news. Newlines
// are the game's line breaks inside one box; collapse them.
function messageLine(state) {
  var msg = String((state && state.message) || "").replace(/\s*\n\s*/g, " ")
  return msg.replace(/^\s+|\s+$/g, "")
}


// ------------------------------------------------------------------ pace

// How long this is taking, and how long it looks like it will take. The whole
// point of the run is partly the claim it supports, so the panel shows the
// estimate WITH its basis -- a range from two data points is worth publishing,
// the same range quoted as though it were measured is not.
function paceLines(state) {
  var p = state && state.projection
  if (!p) return []
  var out = []
  var play = p.play_hours_to_eight_badges
  var real = p.real_hours_to_eight_badges
  if (play) out.push({ label: "8 badges (play)", value: play[0] + "-" + play[1] + " h" })
  if (real) out.push({ label: "8 badges (real)", value: real[0] + "-" + real[1] + " h" })
  if (p.play_hours_to_full_dex)
    out.push({ label: "full dex (play)", value: p.play_hours_to_full_dex + " h" })
  if (out.length === 0)
    out.push({ label: "estimate", value: "not enough evidence yet" })
  return out
}

// The caveat, always shown next to the numbers rather than instead of them.
function paceBasis(state) {
  var p = state && state.projection
  if (!p) return ""
  return [p.badge_basis, p.dex_basis].filter(function (x) { return !!x }).join("; ")
}
