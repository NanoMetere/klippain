"""Execute the real start/prime templates with Klipper snapshot semantics.

Thermal primitives and tool motion are simulated, never attached to hardware.
Assertions inspect executed commands, active-tool render boundaries and physical
coordinates after the pinned backend's base-plus-tool offset transform.
"""
from __future__ import annotations

import copy
import math
import unittest

from toolchanger_harness import Machine, MacroError


def machine(count=7, size=300, actions=None):
    result = Machine(count, size)
    result.vars.update(force_homing_in_start_print=False, verbose=False)
    if actions is not None:
        result.vars['startprint_actions'] = actions
    return result


def prime_state(result, targets, initial=None):
    initial = min(targets) if initial is None else initial
    result.printer['gcode_macro START_PRINT'].update(
        tool_temperatures=dict(targets), initial_tool=initial,
        extruder_temp=targets[initial], primed_tools=[])


def start(result, params):
    result.run('START_PRINT MATERIAL=PLA SOAK=0 ' + params)


def lines(result):
    return [move for move in result.moves if float(move['params'].get('E', 0)) > 0
            and any(axis in move['params'] for axis in 'XY')]


class UsedToolStartTest(unittest.TestCase):
    def test_sparse_usage_preheats_only_used_tools_and_primes_initial_last(self):
        m = machine()
        m.vars['toolchanger_preheat_used_tools'] = True
        start(m, 'INITIAL_TOOL=3 T0_TEMP=215 T3_TEMP=245 T6_TEMP=260 T1_TEMP=0')
        state = m.printer['gcode_macro START_PRINT']
        self.assertEqual({0: 215., 3: 245., 6: 260.}, state['tool_temperatures'])
        self.assertEqual([0, 6, 3], state['primed_tools'])
        self.assertEqual([0, 6, 3], [line['tool'] for line in lines(m)])
        self.assertEqual(3, m.tc['tool_number'])
        self.assertEqual(245, state['extruder_temp'])
        self.assertEqual(245, m.printer['extruder3']['target'])
        self.assertNotIn('PURGE', m.names())
        self.assertNotIn('CLEAN_NOZZLE', m.names())
        tool_targets = [(int(p['T']), float(p['TARGET'])) for n, p in m.commands
                        if n == 'SET_TOOL_TEMPERATURE']
        self.assertEqual({0, 3, 6}, {number for number, _ in tool_targets})
        for number, target in tool_targets:
            self.assertEqual({0: {150., 215.}, 3: {245.}, 6: {260.}}[number] & {target}, {target})
        preheat = m.names().index('_MODULE_EXTRUDER_PREHEATING')
        first_safe_wait = next(i for i, (n, p) in enumerate(m.commands[preheat:], preheat)
                               if n == 'M109')
        preheat_commands = m.commands[preheat:first_safe_wait]
        self.assertEqual([(3, 245.), (6, 260.)],
                         [(int(p['T']), float(p['S'])) for n, p in preheat_commands if n == 'M104'])
        heating = m.names().index('_MODULE_EXTRUDER_HEATING')
        print_wait = next(i for i in range(heating, len(m.commands)) if m.commands[i][0] == 'M109')
        self.assertEqual([(0, 215.), (3, 245.), (6, 260.)],
                         [(int(p['T']), float(p['S'])) for n, p in m.commands[heating:print_wait]
                          if n == 'M104'])
        self.assertEqual(3, m.events[print_wait]['tool'])
        # State updates are visible only to subsequent macro renders. Every
        # canonical PRIMELINE must render after its own tool selection and wait.
        rendered = [r for r in m.renders if r['name'] == 'PRIMELINE']
        self.assertEqual(['extruder', 'extruder6', 'extruder3'], [r['extruder'] for r in rendered])
        self.assertEqual([[], [0], [0, 6]], [r['start']['primed_tools'] for r in rendered])
        for index, (name, params) in enumerate(m.commands):
            if name == 'PRIMELINE':
                number = m.events[index]['tool']
                prior_wait = next(event for event in reversed(m.events[:index]) if event['name'] == 'M109')
                self.assertEqual(number, prior_wait['tool'])
                self.assertEqual(state['tool_temperatures'][number], float(prior_wait['params']['S']))

    def test_unused_t0_has_only_safe_probing_exception_and_no_prime(self):
        m = machine()
        start(m, 'INITIAL_TOOL=3 T3_TEMP=245 T6_TEMP=260')
        self.assertEqual([6, 3], [line['tool'] for line in lines(m)])
        self.assertEqual({150.}, {float(p['TARGET']) for n, p in m.commands
                                 if n == 'SET_TOOL_TEMPERATURE' and p['T'] == '0'})
        self.assertEqual(150, m.printer['extruder']['target'])
        self.assertEqual(221, m.printer['extruder1']['target'])

    def test_every_count_and_bed_primes_all_once(self):
        for count in range(2, 8):
            for size in [300, 350]:
                with self.subTest(count=count, size=size):
                    m = machine(count, size, ('extruder_heating', 'primeline', 'primeline'))
                    targets = ' '.join(f'T{n}_TEMP={210 + n * 5}' for n in range(count))
                    start(m, f'INITIAL_TOOL={count - 1} ' + targets)
                    self.assertEqual(list(range(count)), [line['tool'] for line in lines(m)])
                    self.assertEqual(list(range(count)), m.printer['gcode_macro START_PRINT']['primed_tools'])
                    self.assertEqual(count - 1, m.tc['tool_number'])

    def test_custom_sparse_configured_numbers_do_not_use_array_indices(self):
        m = machine()
        m.tc.update(tool_numbers=[0, 2, 6], tool_names=['tool T0', 'tool T2', 'tool T6'])
        start(m, 'INITIAL_TOOL=2 T0_TEMP=220 T2_TEMP=235 T6_TEMP=250')
        self.assertEqual([0, 6, 2], [line['tool'] for line in lines(m)])
        self.assertEqual(2, m.tc['tool_number'])

    def test_legacy_scalar_only_primes_initial_and_ignores_stale_targets(self):
        for initial in [0, 4]:
            with self.subTest(initial=initial):
                m = machine()
                start(m, f'INITIAL_TOOL={initial} EXTRUDER_TEMP=235')
                self.assertEqual({initial: 235.}, m.printer['gcode_macro START_PRINT']['tool_temperatures'])
                self.assertEqual([initial], [line['tool'] for line in lines(m)])
                self.assertEqual(initial, m.tc['tool_number'])

    def test_second_invocation_resets_used_map_and_completed_list(self):
        m = machine(actions=('extruder_heating', 'primeline'))
        start(m, 'INITIAL_TOOL=3 T0_TEMP=220 T3_TEMP=235 T6_TEMP=250 TOOLS_USED=legacy_mmu_value')
        self.assertEqual('legacy_mmu_value', m.printer['gcode_macro START_PRINT']['tools_used'])
        m.moves.clear()
        start(m, 'INITIAL_TOOL=1 EXTRUDER_TEMP=225')
        self.assertEqual({1: 225.}, m.printer['gcode_macro START_PRINT']['tool_temperatures'])
        self.assertEqual([1], m.printer['gcode_macro START_PRINT']['primed_tools'])
        self.assertEqual([1], [line['tool'] for line in lines(m)])

    def test_single_extruder_start_retains_one_prime_and_scalar_parameters(self):
        m = machine(2, actions=('extruder_heating', 'primeline'))
        m.vars['klippain_toolchanger_enabled'] = False
        start(m, 'EXTRUDER_TEMP=235 INITIAL_TOOL=0 TOOLS_USED=0,1')
        self.assertEqual({}, m.printer['gcode_macro START_PRINT']['tool_temperatures'])
        self.assertEqual('0,1', m.printer['gcode_macro START_PRINT']['tools_used'])
        self.assertEqual(1, len(lines(m)))
        self.assertNotIn('_KLIPPAIN_TOOLCHANGER_PRIMELINES', m.names())
        self.assertNotIn('SELECT_TOOL', m.names())

    def test_custom_action_and_omitting_primeline_remain_supported(self):
        m = machine(actions=('extruder_heating', 'user_defined'))
        m.macros['_START_PRINT_ACTION_USER_DEFINED'] = m.env.from_string('M400')
        m.printer['gcode']['commands'].append('_START_PRINT_ACTION_USER_DEFINED')
        start(m, 'INITIAL_TOOL=3 T3_TEMP=240 T6_TEMP=250')
        self.assertIn('_START_PRINT_ACTION_USER_DEFINED', m.names())
        self.assertEqual([], lines(m))
        self.assertEqual({3: 240., 6: 250.}, m.printer['gcode_macro START_PRINT']['tool_temperatures'])


class ToolchangerStartOptionsTest(unittest.TestCase):
    @staticmethod
    def cold_tools(m):
        for name in m.tc['tool_names']:
            tool = m.printer[name]
            m.printer[tool['heater']].update(target=0., temperature=25.)
            m.printer[tool['extruder']]['can_extrude'] = False

    def test_defaults_and_missing_variables_prime_all_without_bulk_preheat(self):
        for missing in [False, True]:
            with self.subTest(missing=missing):
                m = machine()
                self.assertIs(m.vars['toolchanger_prime_all_used_tools'], True)
                self.assertIs(m.vars['toolchanger_preheat_used_tools'], False)
                if missing:
                    del m.vars['toolchanger_prime_all_used_tools']
                    del m.vars['toolchanger_preheat_used_tools']
                self.cold_tools(m)
                start(m, 'INITIAL_TOOL=3 T0_TEMP=215 T3_TEMP=245 T6_TEMP=260')
                self.assertEqual([0, 6, 3], [line['tool'] for line in lines(m)])
                self.assertFalse(any(n == 'M104' and 'T' in p for n, p in m.commands))
                self.assertEqual(3, m.tc['tool_number'])

    def test_prime_and_preheat_options_are_independent_for_all_four_combinations(self):
        targets = {0: 215., 3: 245., 6: 260.}
        for prime_all in [False, True]:
            for preheat in [False, True]:
                with self.subTest(prime_all=prime_all, preheat=preheat):
                    m = machine()
                    m.vars.update(toolchanger_prime_all_used_tools=prime_all,
                                  toolchanger_preheat_used_tools=preheat)
                    self.cold_tools(m)
                    start(m, 'INITIAL_TOOL=3 T0_TEMP=215 T3_TEMP=245 T6_TEMP=260 T1_TEMP=0')
                    state = m.printer['gcode_macro START_PRINT']
                    expected_lines = [0, 6, 3] if prime_all else [3]
                    self.assertEqual(targets, state['tool_temperatures'])
                    self.assertEqual(expected_lines, state['primed_tools'])
                    self.assertEqual(expected_lines, [line['tool'] for line in lines(m)])
                    self.assertEqual(3, m.tc['tool_number'])
                    preheat_at = m.names().index('_MODULE_EXTRUDER_PREHEATING')
                    heating_at = m.names().index('_MODULE_EXTRUDER_HEATING')
                    prime_at = m.names().index('_MODULE_PRIMELINE')
                    for section, expected in [
                            (m.commands[preheat_at:heating_at], [(3, 245.), (6, 260.)]),
                            (m.commands[heating_at:prime_at], sorted(targets.items()))]:
                        emitted = [(int(p['T']), float(p['S'])) for n, p in section
                                   if n == 'M104' and 'T' in p]
                        self.assertEqual(expected if preheat else [], emitted)
                    self.assertEqual(150., m.events[heating_at]['targets'][0])
                    self.assertEqual(245., m.printer['extruder3']['target'])
                    self.assertEqual(260. if prime_all or preheat else 0.,
                                     m.printer['extruder6']['target'])
                    self.assertEqual(215. if prime_all or preheat else 150.,
                                     m.printer['extruder']['target'])
                    for number in [1, 2, 4, 5]:
                        self.assertEqual(0., m.printer[f'extruder{number}']['target'])
                    if not preheat:
                        for event in m.events:
                            if event['name'] == 'SET_TOOL_TEMPERATURE':
                                # Every required temperature change is for the
                                # tool actually selected at this execution boundary.
                                self.assertEqual(event['tool'], int(event['params']['T']))
                    for index, event in enumerate(m.events):
                        if event['name'] == 'PRIMELINE':
                            wait = next(e for e in reversed(m.events[:index]) if e['name'] == 'M109')
                            self.assertEqual(event['tool'], wait['tool'])
                            self.assertEqual(targets[event['tool']], float(wait['params']['S']))

    def test_unused_t0_stays_at_safe_probe_target_in_all_four_combinations(self):
        for prime_all in [False, True]:
            for preheat in [False, True]:
                with self.subTest(prime_all=prime_all, preheat=preheat):
                    m = machine()
                    m.vars.update(toolchanger_prime_all_used_tools=prime_all,
                                  toolchanger_preheat_used_tools=preheat)
                    self.cold_tools(m)
                    start(m, 'INITIAL_TOOL=3 T3_TEMP=245 T6_TEMP=260')
                    self.assertEqual(150., m.printer['extruder']['target'])
                    self.assertEqual({150.}, {float(p['TARGET']) for n, p in m.commands
                                             if n == 'SET_TOOL_TEMPERATURE' and p['T'] == '0'})
                    self.assertNotIn(0, m.printer['gcode_macro START_PRINT']['primed_tools'])

    def test_initial_t0_in_every_option_mode_with_two_and_seven_tools(self):
        for count in [2, 7]:
            for prime_all in [False, True]:
                for preheat in [False, True]:
                    with self.subTest(count=count, prime_all=prime_all, preheat=preheat):
                        m = machine(count)
                        m.vars.update(toolchanger_prime_all_used_tools=prime_all,
                                      toolchanger_preheat_used_tools=preheat)
                        self.cold_tools(m)
                        last = count - 1
                        start(m, f'INITIAL_TOOL=0 T0_TEMP=215 T{last}_TEMP=260')
                        self.assertEqual([last, 0] if prime_all else [0],
                                         [line['tool'] for line in lines(m)])
                        heating_at = m.names().index('_MODULE_EXTRUDER_HEATING')
                        self.assertEqual(150., m.events[heating_at]['targets'][0])
                        self.assertEqual(215., m.printer['extruder']['target'])
                        self.assertEqual(0, m.tc['tool_number'])

    def test_explicit_later_selection_and_wait_heat_an_unprimed_cold_tool(self):
        for fix in [False, True]:
            with self.subTest(fix=fix):
                m = machine()
                m.vars.update(toolchanger_prime_all_used_tools=False,
                              toolchanger_preheat_used_tools=False,
                              fix_heaters_temperature_settle=fix)
                self.cold_tools(m)
                start(m, 'INITIAL_TOOL=3 T3_TEMP=245 T6_TEMP=260')
                self.assertEqual(0., m.printer['extruder6']['target'])
                self.assertFalse(m.printer['extruder6']['can_extrude'])
                first = len(m.commands)
                m.run('T6\nM109 T6 S260')
                later = m.events[first:]
                select_at = next(i for i, event in enumerate(later) if event['name'] == 'SELECT_TOOL')
                wait_at = next(i for i, event in enumerate(later) if event['name'] == 'M109')
                self.assertLess(select_at, wait_at)
                self.assertEqual(6, later[wait_at]['tool'])
                self.assertEqual(6, m.tc['tool_number'])
                self.assertEqual(260., m.printer['extruder6']['target'])
                self.assertEqual(260., m.printer['extruder6']['temperature'])
                self.assertTrue(m.printer['extruder6']['can_extrude'])
                self.assertEqual([3], m.printer['gcode_macro START_PRINT']['primed_tools'])

    def test_legacy_scalar_fallback_is_one_tool_for_every_option_combination(self):
        for prime_all in [False, True]:
            for preheat in [False, True]:
                for initial in [0, 4]:
                    with self.subTest(prime_all=prime_all, preheat=preheat, initial=initial):
                        m = machine()
                        m.vars.update(toolchanger_prime_all_used_tools=prime_all,
                                      toolchanger_preheat_used_tools=preheat)
                        self.cold_tools(m)
                        start(m, f'INITIAL_TOOL={initial} EXTRUDER_TEMP=235')
                        self.assertEqual({initial: 235.}, m.printer['gcode_macro START_PRINT']['tool_temperatures'])
                        self.assertEqual([initial], [line['tool'] for line in lines(m)])
                        self.assertEqual(initial, m.tc['tool_number'])

    def test_initial_only_layout_ignores_other_used_tools_offsets_and_geometry(self):
        for defect in ['filament', 'cross_section', 'x_offset', 'y_offset', 'z_offset']:
            with self.subTest(defect=defect):
                plans = []
                for targets in [{3: 245.}, {0: 215., 3: 245., 6: 260.}]:
                    m = machine()
                    m.set_active(3)
                    m.vars.update(toolchanger_prime_all_used_tools=False,
                                  prime_line_xy=(295., 295.))
                    for number in [0, 6]:
                        cfg = m.printer['configfile']['settings']['extruder' if number == 0 else 'extruder6']
                        if defect == 'filament':
                            cfg['filament_diameter'] = float('nan')
                        elif defect == 'cross_section':
                            cfg['max_extrude_cross_section'] = .001
                        else:
                            m.printer[f'tool T{number}'][f'gcode_{defect[0]}_offset'] = 500.
                    prime_state(m, targets, initial=3)
                    m.run('_MODULE_PRIMELINE')
                    self.assertEqual([3], [line['tool'] for line in lines(m)])
                    self.assertEqual(targets, m.printer['gcode_macro START_PRINT']['tool_temperatures'])
                    self.assertEqual({3}, {int(p['T']) for n, p in m.commands if n == 'SELECT_TOOL'})
                    self.assertEqual({3}, {event['tool'] for event in m.events if event['name'] == 'M109'})
                    plans.append([(line['gcode_start'], line['gcode_end'], line['params']['E'])
                                  for line in lines(m)])
                self.assertEqual(plans[0], plans[1])

    def test_initial_only_fits_a_strip_too_narrow_for_all_declared_tools(self):
        for prime_all in [False, True]:
            with self.subTest(prime_all=prime_all):
                m = machine()
                m.set_active(3)
                m.vars.update(toolchanger_prime_all_used_tools=prime_all,
                              toolchanger_prime_bed_max=(300., 10.), prime_line_xy=(5., 5.))
                prime_state(m, {n: 220. for n in range(7)}, initial=3)
                if prime_all:
                    with self.assertRaisesRegex(MacroError, 'fit distinct primelines'):
                        m.run('_MODULE_PRIMELINE')
                    self.assertEqual([], lines(m))
                else:
                    m.run('_MODULE_PRIMELINE')
                    self.assertEqual([3], [line['tool'] for line in lines(m)])
                    PrimeGeometryTest().assert_geometry(m)

    def test_completed_tools_preserve_lanes_and_exact_once_in_both_prime_modes(self):
        for prime_all in [False, True]:
            with self.subTest(prime_all=prime_all):
                plans = []
                for completed in [[], [0] if prime_all else [3]]:
                    m = machine()
                    m.vars['toolchanger_prime_all_used_tools'] = prime_all
                    m.printer['configfile']['settings']['extruder']['filament_diameter'] = 2.85
                    prime_state(m, {0: 215., 3: 245., 6: 260.}, initial=3)
                    m.printer['gcode_macro START_PRINT']['primed_tools'] = completed.copy()
                    m.run('_MODULE_PRIMELINE')
                    expected = [0, 6, 3] if prime_all else [3]
                    self.assertEqual(expected, m.printer['gcode_macro START_PRINT']['primed_tools'])
                    self.assertEqual(3, m.tc['tool_number'])
                    plans.append({line['tool']: line['gcode_start'] for line in lines(m)})
                    before = len(lines(m))
                    m.run('_MODULE_PRIMELINE')
                    self.assertEqual(before, len(lines(m)))
                self.assertEqual({k: v for k, v in plans[0].items() if k not in ([0] if prime_all else [3])},
                                 plans[1])

    def test_sparse_physical_numbers_use_contiguous_extruder_objects(self):
        for prime_all in [False, True]:
            for preheat in [False, True]:
                with self.subTest(prime_all=prime_all, preheat=preheat):
                    m = machine(3)
                    old = [copy.deepcopy(m.printer[f'tool T{n}']) for n in range(3)]
                    del m.printer['tool T1']
                    for number, tool in zip([0, 2, 6], old):
                        tool.update(name=f'tool T{number}', tool_number=number)
                        m.printer[f'tool T{number}'] = tool
                    m.tc.update(tool_numbers=[0, 2, 6], tool_names=['tool T0', 'tool T2', 'tool T6'])
                    m.vars.update(toolchanger_prime_all_used_tools=prime_all,
                                  toolchanger_preheat_used_tools=preheat)
                    self.cold_tools(m)
                    start(m, 'INITIAL_TOOL=2 T0_TEMP=215 T2_TEMP=245 T6_TEMP=260')
                    self.assertEqual([0, 6, 2] if prime_all else [2], [line['tool'] for line in lines(m)])
                    self.assertEqual('extruder1', m.printer['toolhead']['extruder'])
                    self.assertEqual(245., m.printer['extruder1']['target'])
                    self.assertEqual(260. if prime_all or preheat else 0., m.printer['extruder2']['target'])
                    self.assertEqual({0: 215., 2: 245., 6: 260.},
                                     m.printer['gcode_macro START_PRINT']['tool_temperatures'])


class StartValidationTest(unittest.TestCase):
    def assert_rejected_before_commands(self, params, setup=None):
        m = machine(3)
        if setup:
            setup(m)
        with self.assertRaises(MacroError):
            start(m, params)
        self.assertEqual(['START_PRINT'], m.names())

    def test_invalid_temperature_values_fail_before_motion_or_heating(self):
        for value in ['-1', 'nan', 'NaN', 'inf', '-inf', '1e309', '', '220,230',
                      '{nozzle_temperature}', 'banana', '+', '.', '1e', '1..2', '--1', 'e']:
            with self.subTest(value=value):
                self.assert_rejected_before_commands(f'T0_TEMP=220 T1_TEMP="{value}"')

    def test_invalid_mode_tool_and_conflict_fail_before_commands(self):
        for params in ['T0_TEMP=0 T1_TEMP=0', 'T1_TEMP=220', 'T0_TEMP=0 T1_TEMP=220',
                       'T0_TEMP=220 T6_TEMP=240', 'T0_TEMP=220 T7_TEMP=0',
                       'T0_TEMP=220 T-1_TEMP=0', 'T0_TEMP=220 T01_TEMP=0',
                       'T0_TEMP=220 T1.0_TEMP=0', 'T0_TEMP=220 EXTRUDER_TEMP=230',
                       'INITIAL_TOOL=3 EXTRUDER_TEMP=220', 'INITIAL_TOOL=banana',
                       'INITIAL_TOOL=1.5', 'INITIAL_TOOL=-1', 'INITIAL_TOOL=1e0']:
            with self.subTest(params=params):
                self.assert_rejected_before_commands(params)

    def test_scalar_input_is_strict_even_with_cold_extrusion_allowed(self):
        def allow_cold(m):
            m.printer['configfile']['settings']['extruder'].update(min_extrude_temp=0, min_temp=0)
        for value in ['', '+', '.', '1e', '1..2', 'nan', 'inf', '0', '-1', '220,230']:
            with self.subTest(value=value):
                self.assert_rejected_before_commands(f'EXTRUDER_TEMP="{value}"', allow_cold)

    def test_each_tools_own_heater_and_extruder_limits_are_used(self):
        def custom_limits(m):
            settings = m.printer['configfile']['settings']
            settings['extruder1'].update(min_extrude_temp=230, max_temp=280)
            m.printer['tool T2']['heater'] = 'aux_heater'
            m.printer['aux_heater'] = dict(target=0., temperature=25.)
            settings['aux_heater'] = dict(min_temp=210, max_temp=245)
        for params in ['T0_TEMP=220 T1_TEMP=225', 'T0_TEMP=220 T1_TEMP=281',
                       'T0_TEMP=220 T2_TEMP=200', 'T0_TEMP=220 T2_TEMP=246']:
            with self.subTest(params=params):
                self.assert_rejected_before_commands(params, custom_limits)

    def test_zero_uninstalled_supported_index_and_scalar_agreement_pass(self):
        m = machine(3, actions=('extruder_heating',))
        start(m, 'T0_TEMP=2.2e2 T6_TEMP=0 EXTRUDER_TEMP=220.01')
        self.assertEqual({0: 220.}, m.printer['gcode_macro START_PRINT']['tool_temperatures'])
        self.assertEqual(220., m.printer['gcode_macro START_PRINT']['extruder_temp'])

    def test_explicit_targets_derive_initial_scalar_without_using_legacy_default(self):
        for default in [0, 'unused_default']:
            with self.subTest(default=default):
                m = machine(3, actions=('extruder_heating',))
                m.vars['print_default_extruder_temp'] = default
                start(m, 'INITIAL_TOOL=1 T0_TEMP=220 T1_TEMP=240')
                self.assertEqual(240., m.printer['gcode_macro START_PRINT']['extruder_temp'])

    def test_invalid_action_order_rejects_before_commands(self):
        for actions in [('primeline',), ('primeline', 'extruder_heating')]:
            with self.subTest(actions=actions):
                self.assert_rejected_before_commands('T0_TEMP=220',
                    lambda m: m.vars.update(startprint_actions=actions))


class PrimeGeometryTest(unittest.TestCase):
    def assert_geometry(self, m):
        output = lines(m)
        settings = m.printer['configfile']['settings']
        bed_min = m.vars.get('toolchanger_prime_bed_min', (0., 0.))
        bed_max = m.vars.get('toolchanger_prime_bed_max',
                             (m.printer['toolhead']['axis_maximum'].x, m.printer['toolhead']['axis_maximum'].y))
        axis_min = m.printer['toolhead']['axis_minimum']
        axis_max = m.printer['toolhead']['axis_maximum']
        for move in m.moves:
            for index, axis in enumerate('xyz'):
                self.assertTrue(math.isfinite(move['end'][index]))
                self.assertGreaterEqual(move['end'][index], getattr(axis_min, axis) - 1e-7)
                self.assertLessEqual(move['end'][index], getattr(axis_max, axis) + 1e-7)
        for move in output:
            diameter = settings[m.printer[f"tool T{move['tool']}"]['extruder']]['filament_diameter']
            length = math.dist(move['gcode_start'][:2], move['gcode_end'][:2])
            width = float(move['params']['E']) * math.pi * (diameter / 2)**2 / (length * m.vars['prime_line_height'])
            # Product uses 3.14159; roundoff tolerance is sub-micron.
            for point in [move['gcode_start'], move['gcode_end']]:
                for axis in range(2):
                    self.assertGreaterEqual(point[axis] - width / 2, bed_min[axis] - 1e-5)
                    self.assertLessEqual(point[axis] + width / 2, bed_max[axis] + 1e-5)
        cross_axis = 1 if m.vars['prime_line_direction'] == 'X' else 0
        ordered = sorted(output, key=lambda move: move['gcode_start'][cross_axis])
        for a, b in zip(ordered, ordered[1:]):
            widths = []
            for move in [a, b]:
                cfg = settings[m.printer[f"tool T{move['tool']}"]['extruder']]
                volume = float(move['params']['E']) * math.pi * (cfg['filament_diameter'] / 2)**2
                widths.append(volume / (m.vars['prime_line_length'] * m.vars['prime_line_height']))
            gap = b['gcode_start'][cross_axis] - a['gcode_start'][cross_axis] - sum(widths) / 2
            self.assertGreaterEqual(gap, 2. - 1e-5)

    def test_all_lanes_fit_at_edges_in_both_orientations_with_wipe(self):
        for direction in ['X', 'Y']:
            for corner in [(0., 0.), (300., 300.), (0., 300.), (300., 0.)]:
                with self.subTest(direction=direction, corner=corner):
                    m = machine()
                    m.vars.update(prime_line_direction=direction, prime_line_xy=corner, prime_line_wipe=True)
                    prime_state(m, {n: 220. for n in range(7)}, initial=3)
                    m.run('_MODULE_PRIMELINE')
                    self.assertEqual([0, 1, 2, 4, 5, 6, 3], [line['tool'] for line in lines(m)])
                    self.assert_geometry(m)

    def test_offsets_asymmetric_axes_and_true_bed_bounds(self):
        for direction in ['X', 'Y']:
            with self.subTest(direction=direction):
                m = machine()
                m.vars.update(prime_line_direction=direction, prime_line_xy=(340., -100.),
                              prime_line_wipe=True, toolchanger_prime_bed_min=(15., 20.),
                              toolchanger_prime_bed_max=(270., 255.))
                limits = m.printer['toolhead']
                limits['axis_minimum'].x, limits['axis_minimum'].y = -20., 10.
                limits['axis_maximum'].x, limits['axis_maximum'].y = 310., 290.
                m.run('SET_GCODE_OFFSET X=13 Y=-7 Z=0.2')
                for n in range(7):
                    m.printer[f'tool T{n}'].update(gcode_x_offset=(-1)**n * (n + 1) * 2.,
                                                 gcode_y_offset=(-1)**(n + 1) * (n + 1),
                                                 gcode_z_offset=(n - 3) * 0.1)
                prime_state(m, {0: 220., 2: 235., 6: 250.}, initial=2)
                m.run('_MODULE_PRIMELINE')
                self.assert_geometry(m)

    def test_each_extruder_geometry_and_effective_default_is_used(self):
        m = machine(3)
        settings = m.printer['configfile']['settings']
        settings['extruder1'].update(filament_diameter=2.85, max_extrude_cross_section=1.5)
        settings['extruder2'].update(nozzle_diameter=.8, max_extrude_cross_section=2.56)
        m.printer['configfile']['config'] = copy.deepcopy(settings)
        del m.printer['configfile']['config']['extruder2']['max_extrude_cross_section']
        prime_state(m, {0: 220., 1: 240., 2: 250.}, initial=0)
        m.run('_MODULE_PRIMELINE')
        output = {line['tool']: line for line in lines(m)}
        self.assertEqual(30., float(output[0]['params']['E']))
        expected = .98 * 1.5 * 40 / (3.14159 * (2.85 / 2)**2)
        self.assertAlmostEqual(expected, float(output[1]['params']['E']))
        self.assertEqual(30., float(output[2]['params']['E']))
        self.assert_geometry(m)

    def test_adaptive_size_and_exclude_object_use_same_preferred_group(self):
        starts = []
        for mode in ['size', 'objects']:
            m = machine(3)
            prime_state(m, {0: 220., 1: 230., 2: 240.}, initial=2)
            if mode == 'objects':
                m.printer['exclude_object'] = dict(objects=[dict(polygon=[(240., 230.), (280., 230.), (280., 270.), (240., 270.)])])
                m.run('_KLIPPAIN_TOOLCHANGER_PRIMELINES ADAPTIVE_MODE=1')
            else:
                m.run('_KLIPPAIN_TOOLCHANGER_PRIMELINES SIZE=240_230_280_270 ADAPTIVE_MODE=1')
            self.assert_geometry(m)
            starts.append([line['gcode_start'] for line in lines(m)])
        self.assertEqual(starts[0], starts[1])

    def test_impossible_or_invalid_geometry_rejects_before_selection_or_extrusion(self):
        cases = [('prime_line_length', 400.), ('prime_line_height', 0.),
                 ('prime_line_purge_distance', 0.), ('prime_line_flowrate', 0.),
                 ('prime_line_length', float('nan')), ('prime_line_height', float('inf')),
                 ('prime_line_purge_distance', float('nan')), ('prime_line_flowrate', float('inf')),
                 ('prime_line_direction', 'Z'), ('toolchanger_prime_bed_max', (45., 12.))]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                m = machine()
                m.vars[key] = value
                prime_state(m, {n: 220. for n in range(7)}, initial=3)
                with self.assertRaises(MacroError):
                    m.run('_MODULE_PRIMELINE')
                self.assertEqual([], m.moves)
                self.assertNotIn('SELECT_TOOL', m.names())
                self.assertEqual([], m.printer['gcode_macro START_PRINT']['primed_tools'])

    def test_later_tool_invalid_width_or_z_rejects_whole_plan_before_first_line(self):
        for defect in ['thin', 'zmin', 'zmax']:
            with self.subTest(defect=defect):
                m = machine(3)
                prime_state(m, {0: 220., 1: 230., 2: 240.}, initial=2)
                if defect == 'thin':
                    m.printer['configfile']['settings']['extruder2']['max_extrude_cross_section'] = .5
                else:
                    m.printer['tool T2']['gcode_z_offset'] = -1. if defect == 'zmin' else 299.
                with self.assertRaises(MacroError):
                    m.run('_MODULE_PRIMELINE')
                self.assertEqual([], lines(m))
                self.assertNotIn('SELECT_TOOL', m.names())

    def test_low_z_clearance_and_z_hop_are_physical_and_bounded(self):
        m = machine(3)
        m.run('SET_GCODE_OFFSET Z=0.2\nG0 Z0.6')
        m.printer['tool T1']['gcode_z_offset'] = .4
        m.printer['tool T2']['gcode_z_offset'] = -.2
        prime_state(m, {0: 220., 1: 230., 2: 240.}, initial=2)
        m.run('_MODULE_PRIMELINE')
        self.assert_geometry(m)
        for index, move in enumerate(m.moves):
            if 'E' in move['params'] and any(axis in move['params'] for axis in 'XY'):
                subsequent_z = next(later for later in m.moves[index + 1:] if 'Z' in later['params'])
                self.assertAlmostEqual(move['end'][2] + 3., subsequent_z['end'][2])

    def test_safe_high_tool_z_offset_does_not_require_unneeded_initial_raise(self):
        m = machine(2)
        m.printer['toolhead']['axis_maximum'].z = 10.
        m.printer['tool T0']['gcode_z_offset'] = 6.
        m.run('G0 Z4')
        self.assertEqual(10., m.printer['toolhead']['position'].z)
        prime_state(m, {0: 220.})
        m.run('_MODULE_PRIMELINE')
        self.assertEqual([0], [line['tool'] for line in lines(m)])
        self.assert_geometry(m)

    def test_partial_completion_preserves_original_lanes_and_first_pending_z(self):
        plans = []
        for completed in [[], [0]]:
            m = machine(3)
            m.printer['configfile']['settings']['extruder']['filament_diameter'] = 2.85
            prime_state(m, {0: 220., 1: 230., 2: 240.}, initial=2)
            m.printer['gcode_macro START_PRINT']['primed_tools'] = completed.copy()
            if completed:
                m.run('G0 Z3.6')
            m.run('_MODULE_PRIMELINE')
            plans.append({line['tool']: line['gcode_start'] for line in lines(m)})
            self.assertEqual([0, 1, 2], m.printer['gcode_macro START_PRINT']['primed_tools'])
            self.assert_geometry(m)
        self.assertEqual({1: plans[0][1], 2: plans[0][2]}, plans[1])

    def test_detection_loss_during_heating_aborts_before_line_and_progress(self):
        m = machine(2)
        prime_state(m, {0: 220., 1: 230.}, initial=0)
        def lose_detection(machine, params):
            if params.get('WAIT') == '1':
                machine.tc['detected_tool_number'] = -1
                machine.tc['detected_tool'] = None
        m.command_hooks['SET_TOOL_TEMPERATURE'] = lose_detection
        with self.assertRaises(MacroError):
            m.run('_MODULE_PRIMELINE')
        self.assertIn('M109', m.names())
        self.assertEqual([], lines(m))
        self.assertEqual([], m.printer['gcode_macro START_PRINT']['primed_tools'])
        self.assertNotIn('_KLIPPAIN_PRIME_TOOL_COMPLETE', m.names())

    def test_wrong_detected_tool_cannot_be_marked_complete(self):
        m = machine(2)
        prime_state(m, {0: 220., 1: 230.})
        m.tc['detected_tool_number'] = 1
        with self.assertRaises(MacroError):
            m.run('_KLIPPAIN_PRIME_TOOL_COMPLETE T=0')
        self.assertEqual([], m.printer['gcode_macro START_PRINT']['primed_tools'])

    def test_unhomed_preflight_fails_before_any_motion(self):
        m = machine()
        prime_state(m, {0: 220., 6: 250.})
        m.printer['toolhead']['homed_axes'] = 'xy'
        with self.assertRaisesRegex(MacroError, 'Home XYZ'):
            m.run('_MODULE_PRIMELINE')
        self.assertEqual([], m.moves)

    def test_selection_failure_aborts_prime_and_manual_resume_does_not_retry(self):
        m = machine(3)
        prime_state(m, {0: 220., 1: 230., 2: 240.}, initial=0)
        m.fail_next = True
        with self.assertRaisesRegex(MacroError, 'remaining macro operations'):
            m.run('_MODULE_PRIMELINE')
        self.assertNotIn('PRIMELINE', m.names())
        self.assertNotIn('_KLIPPAIN_PRIME_TOOL_COMPLETE', m.names())
        self.assertEqual([], m.printer['gcode_macro START_PRINT']['primed_tools'])
        self.assertEqual(1, m.state['target_tool'])
        self.assertEqual(0, m.state['previous_tool'])
        before = m.names().count('SELECT_TOOL')
        m.tc['detected_tool_number'] = 1
        m.run('RESUME')
        self.assertEqual(before, m.names().count('SELECT_TOOL'))
        self.assertEqual(1, m.native_resumes)

    def test_start_during_pending_recovery_preserves_native_pause_and_progress(self):
        m = machine(3)
        prime_state(m, {0: 220., 1: 235.}, initial=0)
        m.printer['gcode_macro START_PRINT']['primed_tools'] = [0]
        m.fail_next = True
        m.run('T1')
        self.assertTrue(m.printer['pause_resume']['is_paused'])
        previous_state = copy.deepcopy(m.state)
        previous_start = copy.deepcopy(m.printer['gcode_macro START_PRINT'])
        native_pause = copy.deepcopy(m.saved['PAUSE_STATE'])
        command_count = len(m.commands)
        with self.assertRaisesRegex(MacroError, 'recovery is pending'):
            start(m, 'INITIAL_TOOL=1 T1_TEMP=245')
        self.assertEqual(['START_PRINT'], m.names()[command_count:])
        self.assertTrue(m.printer['pause_resume']['is_paused'])
        self.assertEqual(previous_state, m.state)
        self.assertEqual(previous_start, m.printer['gcode_macro START_PRINT'])
        self.assertEqual(native_pause, m.saved['PAUSE_STATE'])
        before = m.names().count('SELECT_TOOL')
        m.tc['detected_tool_number'] = 1
        m.run('RESUME')
        self.assertEqual(before, m.names().count('SELECT_TOOL'))
        self.assertEqual(1, m.native_resumes)


if __name__ == '__main__':
    unittest.main()
