"""Frozen compatibility contract for the public Driver facade."""

import inspect

import pytest

import trek
from crystalagent.driver import Driver as PackageDriver
from crystalagent.driver import inventory, navigation
from crystalagent import nav
from crystalagent.decide import DecisionRequired as OwnedDecisionRequired

pytestmark = pytest.mark.unit


def _stable(value):
    if value is inspect.Parameter.empty:
        return ("required",)
    if type(value) is object:
        return ("opaque", "object")
    if isinstance(value, dict):
        return ("dict", tuple(sorted((_stable(k), _stable(v)) for k, v in value.items())))
    if isinstance(value, (set, frozenset)):
        return (type(value).__name__, tuple(sorted(_stable(v) for v in value)))
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, tuple(_stable(v) for v in value))
    return (type(value).__name__, value)


def _method_signature(value):
    return tuple(
        (parameter.name, parameter.kind.name, _stable(parameter.default))
        for parameter in inspect.signature(value).parameters.values()
    )


METHODS = {'battle': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'battle_frame': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'blocked_by': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'blocked_cells': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                   ('map_name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'boulder_cells': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'box_list': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'boxes': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'can_push': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'catch': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('ball', 'POSITIONAL_OR_KEYWORD', ('str', 'POKE BALL')),
           ('max_balls', 'POSITIONAL_OR_KEYWORD', ('int', 10)),
           ('nickname', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'catch_up': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('nickname', 'POSITIONAL_OR_KEYWORD', ('NoneType', None)),
              ('ball', 'POSITIONAL_OR_KEYWORD', ('str', 'POKE BALL')),
              ('max_balls', 'POSITIONAL_OR_KEYWORD', ('int', 6)),
              ('max_encounters', 'POSITIONAL_OR_KEYWORD', ('int', 12)),
              ('label', 'POSITIONAL_OR_KEYWORD', ('str', ''))),
 'change_box': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                ('n', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'clear_obstacle': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                    ('direction', 'POSITIONAL_OR_KEYWORD', ('required',)),
                    ('tries', 'POSITIONAL_OR_KEYWORD', ('int', 6))),
 'close_menus': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('max_presses', 'POSITIONAL_OR_KEYWORD', ('int', 14))),
 'cursor_rows': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'cut': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
         ('x', 'POSITIONAL_OR_KEYWORD', ('NoneType', None)),
         ('y', 'POSITIONAL_OR_KEYWORD', ('NoneType', None)),
         ('facing', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'dark_maps': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'default_learn_policy': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                          ('mon', 'POSITIONAL_OR_KEYWORD', ('required',)),
                          ('new_move', 'POSITIONAL_OR_KEYWORD', ('required',)),
                          ('current', 'POSITIONAL_OR_KEYWORD', ('required',))),
 'deposit': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('mon', 'POSITIONAL_OR_KEYWORD', ('required',))),
 'dismiss_keyboard': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                      ('name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'drain_scene': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('max_frames', 'POSITIONAL_OR_KEYWORD', ('int', 6000))),
 'enable_surf': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'engine_flag': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('name', 'POSITIONAL_OR_KEYWORD', ('required',))),
 'exits': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('map_name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'explore_bfs': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('goal', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('max_moves', 'POSITIONAL_OR_KEYWORD', ('int', 600)),
                 ('dirs', 'POSITIONAL_OR_KEYWORD', ('str', 'URDL')),
                 ('forbid_maps', 'POSITIONAL_OR_KEYWORD', ('tuple', ())),
                 ('on_battle', 'POSITIONAL_OR_KEYWORD', ('str', 'fight')),
                 ('max_nodes', 'POSITIONAL_OR_KEYWORD', ('int', 400))),
 'face': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
          ('mv', 'POSITIONAL_OR_KEYWORD', ('required',))),
 'facing': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'field_moves': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'fight': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('max_frames', 'POSITIONAL_OR_KEYWORD', ('int', 90000)),
           ('policy', 'POSITIONAL_OR_KEYWORD', ('NoneType', None)),
           ('require_decision', 'POSITIONAL_OR_KEYWORD', ('bool', False)),
           ('consult_encounter', 'POSITIONAL_OR_KEYWORD', ('bool', True)),
           ('resume', 'POSITIONAL_OR_KEYWORD', ('int', 4))),
 'find_tiles': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                ('kind', 'POSITIONAL_OR_KEYWORD', ('required',)),
                ('map_name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'flush_dialog': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                  ('max_frames', 'POSITIONAL_OR_KEYWORD', ('int', 6000)),
                  ('quiet_frames', 'POSITIONAL_OR_KEYWORD', ('int', 40))),
 'goto': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
          ('x', 'POSITIONAL_OR_KEYWORD', ('required',)),
          ('y', 'POSITIONAL_OR_KEYWORD', ('required',)),
          ('label', 'POSITIONAL_OR_KEYWORD', ('str', '')),
          ('map_name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None)),
          ('strict', 'POSITIONAL_OR_KEYWORD', ('bool', False)),
          ('escalate', 'POSITIONAL_OR_KEYWORD', ('bool', True))),
 'grid_drift': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'gym_scout': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('map', 'POSITIONAL_OR_KEYWORD', ('required',))),
 'heal': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
          ('tries', 'POSITIONAL_OR_KEYWORD', ('int', 2))),
 'heal_party': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                ('items', 'POSITIONAL_OR_KEYWORD', ('NoneType', None)),
                ('max_items_per_mon', 'POSITIONAL_OR_KEYWORD', ('int', 6))),
 'item_sources': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'keyboard_open': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'lead': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'live_attach': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('kw', 'VAR_KEYWORD', ('required',))),
 'live_detach': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'live_grid': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'map_name': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'map_objects': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('map_name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'map_view': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('map_name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'mart_buy': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('x', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('y', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('item_name', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('qty', 'POSITIONAL_OR_KEYWORD', ('int', 1)),
              ('label', 'POSITIONAL_OR_KEYWORD', ('str', ''))),
 'menu_open': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'missables': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('kind', 'POSITIONAL_OR_KEYWORD', ('str', 'key'))),
 'move_id': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('name', 'POSITIONAL_OR_KEYWORD', ('required',))),
 'move_power': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                ('name', 'POSITIONAL_OR_KEYWORD', ('required',))),
 'move_settled': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                  ('mv', 'POSITIONAL_OR_KEYWORD', ('required',)),
                  ('hold', 'POSITIONAL_OR_KEYWORD', ('int', 40)),
                  ('max_frames', 'POSITIONAL_OR_KEYWORD', ('int', 600)),
                  ('fight', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'name_prompt': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('name', 'POSITIONAL_OR_KEYWORD', ('required',))),
 'needs_flash': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('map_name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'npc_cells': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'observe': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'outlook': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'pace': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
          ('steps', 'POSITIONAL_OR_KEYWORD', ('required',)),
          ('dirs', 'POSITIONAL_OR_KEYWORD', ('str', 'UDLR')),
          ('box', 'POSITIONAL_OR_KEYWORD', ('NoneType', None)),
          ('on_battle', 'POSITIONAL_OR_KEYWORD', ('str', 'return'))),
 'party_swap': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                ('row_a', 'POSITIONAL_OR_KEYWORD', ('required',)),
                ('row_b', 'POSITIONAL_OR_KEYWORD', ('required',))),
 'pocket_tag': (('tag', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'pos': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'press': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('seq', 'POSITIONAL_OR_KEYWORD', ('required',))),
 'reach': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('x', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('y', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('label', 'POSITIONAL_OR_KEYWORD', ('str', '')),
           ('budget', 'POSITIONAL_OR_KEYWORD', ('int', 200)),
           ('nodes', 'POSITIONAL_OR_KEYWORD', ('int', 140))),
 'resolve_choice': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                    ('choice', 'POSITIONAL_OR_KEYWORD', ('str', 'YES'))),
 'route': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('dest_map', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('max_cost', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'save': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
          ('name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None)),
          ('force', 'POSITIONAL_OR_KEYWORD', ('bool', False))),
 'scene_busy': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'select_menu_row': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                     ('label', 'POSITIONAL_OR_KEYWORD', ('required',)),
                     ('max_presses', 'POSITIONAL_OR_KEYWORD', ('int', 14)),
                     ('confirm', 'POSITIONAL_OR_KEYWORD', ('bool', True)),
                     ('match', 'POSITIONAL_OR_KEYWORD', ('NoneType', None)),
                     ('confirm_seq', 'POSITIONAL_OR_KEYWORD', ('str', 'A:6 .:18'))),
 'set_text_speed': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                    ('mode', 'POSITIONAL_OR_KEYWORD', ('str', 'FAST'))),
 'settle': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
            ('quiet', 'POSITIONAL_OR_KEYWORD', ('int', 3)),
            ('spacing', 'POSITIONAL_OR_KEYWORD', ('int', 20)),
            ('max_frames', 'POSITIONAL_OR_KEYWORD', ('int', 900))),
 'species_tmhm': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'sprite_cell': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('sprite', 'POSITIONAL_OR_KEYWORD', ('required',)),
                 ('map_name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'sprites': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'status': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
            ('missing', 'POSITIONAL_OR_KEYWORD', ('bool', True))),
 'step_dir': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('mv', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('max_frames', 'POSITIONAL_OR_KEYWORD', ('int', 40))),
 'step_hold': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('mv', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('hold', 'POSITIONAL_OR_KEYWORD', ('int', 80))),
 'step_off_warp': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'sync_grid': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'take_warp': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('x', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('y', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('label', 'POSITIONAL_OR_KEYWORD', ('str', ''))),
 'talk_to': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('x', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('y', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('label', 'POSITIONAL_OR_KEYWORD', ('str', '')),
             ('facing', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'teach_hm': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('hm_tag', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('move_name', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('forget_move', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'teach_tm': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('tm', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('mon', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('forget', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'textbox': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'tile_at': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('x', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('y', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('map_name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'tiles_in': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('x0', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('y0', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('x1', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('y1', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('map_name', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'tmhm_moves': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'tmhm_stock': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'train': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('target_level', 'POSITIONAL_OR_KEYWORD', ('required',)),
           ('max_battles', 'POSITIONAL_OR_KEYWORD', ('int', 150)),
           ('targets', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'travel': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
            ('dest_map', 'POSITIONAL_OR_KEYWORD', ('required',)),
            ('label', 'POSITIONAL_OR_KEYWORD', ('str', ''))),
 'type_name': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('name', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('max_len', 'POSITIONAL_OR_KEYWORD', ('int', 10))),
 'use_cut': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('tree_x', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('tree_y', 'POSITIONAL_OR_KEYWORD', ('required',)),
             ('label', 'POSITIONAL_OR_KEYWORD', ('str', '')),
             ('forget_move', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'use_field_move': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
                    ('move', 'POSITIONAL_OR_KEYWORD', ('required',)),
                    ('facing', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'use_item': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('item_name', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('target_slot', 'POSITIONAL_OR_KEYWORD', ('opaque', 'object')),
              ('field', 'POSITIONAL_OR_KEYWORD', ('bool', True)),
              ('mon', 'KEYWORD_ONLY', ('NoneType', None))),
 'walk': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
          ('path', 'POSITIONAL_OR_KEYWORD', ('required',)),
          ('label', 'POSITIONAL_OR_KEYWORD', ('str', ''))),
 'waterfall': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('facing', 'POSITIONAL_OR_KEYWORD', ('str', 'U'))),
 'whirlpool': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
               ('facing', 'POSITIONAL_OR_KEYWORD', ('NoneType', None))),
 'who_fights': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),),
 'withdraw': (('self', 'POSITIONAL_OR_KEYWORD', ('required',)),
              ('mon', 'POSITIONAL_OR_KEYWORD', ('required',)))}

PROPERTIES = ("tactics",)

CONFIGS = {'BALL_PREFERENCE': ('tuple', (('str', 'POKE BALL'), ('str', 'GREAT BALL'), ('str', 'ULTRA BALL'))),
 'BATTLE_SHIFT_BIT': ('int', 6),
 'BOULDER_MOVEMENT': ('int', 25),
 'BOX_COUNT': ('int', 14),
 'DEFAULT_MAX_COST': ('int', 700),
 'EDGE_SLIDE': ('int', 6),
 'FACING_BYTE': ('dict',
                 ((('str', 'D'), ('int', 0)),
                  (('str', 'L'), ('int', 8)),
                  (('str', 'R'), ('int', 12)),
                  (('str', 'U'), ('int', 4)))),
 'FIGHT_DIAG_CAP': ('int', 3),
 'FORGET_PRIORITY': ('list',
                     (('str', 'SMOKESCREEN'),
                      ('str', 'LEER'),
                      ('str', 'GROWL'),
                      ('str', 'CHARM'),
                      ('str', 'TAIL WHIP'),
                      ('str', 'DEFENSE CURL'),
                      ('str', 'SAND-ATTACK'),
                      ('str', 'TACKLE'),
                      ('str', 'MUD-SLAP'),
                      ('str', 'QUICK ATTACK'),
                      ('str', 'BUBBLE'),
                      ('str', 'EMBER'),
                      ('str', 'SWIFT'))),
 'FREE_HIT_LOUD': ('int', 2),
 'GOTO_ESCALATE_MOVES': ('int', 60),
 'GOTO_ESCALATE_NODES': ('int', 40),
 'GOTO_ESCALATE_ON': ('tuple',
                      (('str', 'no-path'),
                       ('str', 'unreachable'),
                       ('str', 'replan-storm'),
                       ('str', 'no-progress'),
                       ('str', 'pass-cap'),
                       ('str', 'outside-bounds'))),
 'GOTO_HANDOFF': ('tuple', (('str', 'manual'), ('str', 'choice menu'), ('str', 'whiteout'))),
 'GOTO_NO_ESCALATE_ON': ('tuple',
                         (('str', 'npc'),
                          ('str', 'target-occupied'),
                          ('str', 'script-scene-active'),
                          ('str', 'choice menu'),
                          ('str', 'whiteout'),
                          ('str', 'manual'),
                          ('str', 'waited-for-wanderer'))),
 'HM_MOVES': ('frozenset',
              (('str', 'CUT'),
               ('str', 'FLASH'),
               ('str', 'FLY'),
               ('str', 'STRENGTH'),
               ('str', 'SURF'),
               ('str', 'WATERFALL'),
               ('str', 'WHIRLPOOL'))),
 'OW_FIELD_MOVES': ('dict',
                    ((('str', 'CUT'), ('tuple', (('str', 'cut-tree'), ('str', 'HIVE')))),
                     (('str', 'WATERFALL'), ('tuple', (('str', 'waterfall'), ('str', 'RISING')))),
                     (('str', 'WHIRLPOOL'),
                      ('tuple', (('str', 'whirlpool'), ('str', 'GLACIER')))))),
 'PC_LIST_STATE': ('int', 1),
 'PC_PROMPT_ROW': ('int', 16),
 'PC_SUBMENU_STATE': ('int', 3),
 'TRANSITION_COST': ('int', 60),
 'WANDER_WAIT_CHUNK': ('int', 150),
 'WANDER_WAIT_FRAMES': ('int', 600),
 'auto_fight_steps': ('bool', False),
 'decide_all': ('bool', False),
 'encounter_policy': ('NoneType', None),
 'last_battle': ('NoneType', None),
 'last_field_reason': ('NoneType', None),
 'last_frame': ('NoneType', None),
 'last_goto_reason': ('NoneType', None),
 'last_item_reason': ('NoneType', None),
 'last_menu_reason': ('NoneType', None),
 'last_money_delta': ('int', 0),
 'last_pc_reason': ('NoneType', None),
 'last_step_reason': ('NoneType', None),
 'last_tm_reason': ('NoneType', None),
 'last_warp_reason': ('NoneType', None),
 'learn_moves': ('bool', True),
 'learn_policy': ('NoneType', None),
 'trip_scenes': ('bool', False)}

ROOT_EXPORTS = (
    "Driver", "DecisionRequired", "TravelError", "HealError", "TrekNav",
    "mapgraph", "render_map_view", "script_is_disruptive",
    "script_advances_scene", "script_guards", "coord_events", "scene_consts",
    "scene_vars", "heal_pokecenter",
)


def _assert_public_contract(driver_class):
    public = {
        name: value for name, value in inspect.getmembers(driver_class)
        if not name.startswith("_")
    }
    assert {
        name: _method_signature(value)
        for name, value in public.items()
        if callable(value)
    } == METHODS
    assert tuple(sorted(
        name for name, value in public.items() if isinstance(value, property)
    )) == PROPERTIES
    assert {
        name: _stable(value)
        for name, value in public.items()
        if not callable(value) and not isinstance(value, property)
    } == CONFIGS


def test_public_driver_contract_is_unchanged():
    _assert_public_contract(trek.Driver)
    _assert_public_contract(PackageDriver)


def test_driver_mixins_have_unique_attributes():
    owners = {}
    for mixin in PackageDriver.__bases__:
        for name in vars(mixin):
            if name.startswith("__"):
                continue
            assert name not in owners, (
                f"{name} is owned by both {owners[name]} and {mixin.__name__}"
            )
            owners[name] = mixin.__name__


def test_intentional_root_exports_are_owner_objects():
    assert trek.Driver is PackageDriver
    expected = {
        "DecisionRequired": OwnedDecisionRequired,
        "TravelError": navigation.TravelError,
        "HealError": inventory.HealError,
        "TrekNav": nav.TrekNav,
        "mapgraph": nav.mapgraph,
        "render_map_view": nav.render_map_view,
        "script_is_disruptive": nav.script_is_disruptive,
        "script_advances_scene": nav.script_advances_scene,
        "script_guards": nav.script_guards,
        "coord_events": nav.coord_events,
        "scene_consts": nav.scene_consts,
        "scene_vars": nav.scene_vars,
        "heal_pokecenter": inventory.heal_pokecenter,
    }
    for name, value in expected.items():
        assert getattr(trek, name) is value
