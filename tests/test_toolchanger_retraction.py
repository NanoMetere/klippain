"""Outgoing-tool retraction and recorded per-tool repayment regressions.

The repository templates run in the render/execute harness; motion and heating
are modelled, so these checks do not claim physical extrusion or docking safety.
"""
from __future__ import annotations

import copy
import unittest

from toolchanger_harness import Machine, MacroError, config


def extrusion_moves(machine):
    return [(move['tool'], float(move['params']['E']))
            for move in machine.moves if 'E' in move['params']]


def load_real_purge(machine):
    """Use the real purge/brush templates, which the general harness stubs."""
    parsed = config('macros/helpers/nozzle_cleaning.cfg')
    for section in parsed.sections():
        if section.startswith('gcode_macro '):
            name = section.split(' ', 1)[1]
            machine.macros[name] = machine.env.from_string(parsed.get(section, 'gcode'))
    machine.vars.update(purge_and_brush_enabled=True, purgeclean_servo_enabled=False,
                        verbose=False)


class ToolchangerRetractionTest(unittest.TestCase):
    def test_retraction_option_defaults_enabled_and_uses_end_length_and_speed(self):
        m = Machine()
        self.assertTrue(m.vars['toolchanger_retract_on_change'])
        self.assertEqual(20, m.vars['retract_length'])
        m.run('T1')
        self.assertEqual([(0, -20)], extrusion_moves(m))
        motion = next(move for move in m.moves if 'E' in move['params'])
        self.assertEqual(2100, float(motion['params']['F']))
        self.assertNotIn('M109', m.names())
        self.assertNotIn('SET_TOOL_TEMPERATURE', m.names())

    def test_existing_variables_without_new_option_keep_default_retraction(self):
        m = Machine()
        del m.vars['toolchanger_retract_on_change']
        m.run('T1')
        self.assertEqual([(0, -20)], extrusion_moves(m))

    def test_zero_length_skips_and_invalid_lengths_reject_before_motion_state_changes(self):
        m = Machine()
        m.vars['retract_length'] = 0
        m.run('T1')
        self.assertEqual([], extrusion_moves(m))
        self.assertEqual({}, m.state['retract_debts'])
        for value in [-1, float('nan'), float('inf'), -float('inf')]:
            with self.subTest(value=value):
                m = Machine()
                m.vars['retract_length'] = value
                with self.assertRaisesRegex(MacroError, 'finite, nonnegative'):
                    m.run('T1')
                self.assertEqual([], extrusion_moves(m))
                self.assertEqual({}, m.state['retract_debts'])
                self.assertFalse({'M220', 'M221', 'M83'} & set(m.names()))

    def test_hot_return_restores_only_the_recorded_tool_once(self):
        m = Machine()
        m.run('T1\nT0\nT0\nM109 T0 S220')
        self.assertEqual([(0, -20), (1, -20), (0, 20)], extrusion_moves(m))

    def test_lengths_are_recorded_per_tool_and_do_not_follow_later_setting(self):
        m = Machine(3)
        m.run('T1')
        m.vars['retract_length'] = 5.5
        m.run('T2')
        m.vars['retract_length'] = 12
        m.run('T0\nT1')
        self.assertEqual([(0, -20), (1, -5.5), (2, -12), (0, 20),
                          (0, -12), (1, 5.5)], extrusion_moves(m))

    def test_disabled_option_prevents_new_swap_retraction(self):
        m = Machine()
        m.vars['toolchanger_retract_on_change'] = False
        m.run('T1\nT0')
        self.assertEqual([], extrusion_moves(m))

    def test_disabling_option_still_repays_previously_completed_retraction(self):
        m = Machine()
        m.run('T1')
        m.vars['toolchanger_retract_on_change'] = False
        m.run('T0')
        self.assertEqual([(0, -20), (0, 20)], extrusion_moves(m))

    def test_same_tool_and_initialization_do_not_retract_or_repay(self):
        m = Machine()
        m.run('T0\nINITIALIZE_TOOLCHANGER T=0\nINITIALIZE_TOOLCHANGER T=1')
        self.assertEqual([], extrusion_moves(m))
        m.run('T0')
        before = list(extrusion_moves(m))
        m.run('INITIALIZE_TOOLCHANGER T=1')
        self.assertEqual(before, extrusion_moves(m))

    def test_cold_outgoing_does_not_heat_or_create_repayment(self):
        m = Machine()
        m.printer['extruder'].update(temperature=35, target=0, can_extrude=False)
        m.run('T1')
        self.assertEqual([], extrusion_moves(m))
        self.assertNotIn('M109', m.names())
        self.assertNotIn('SET_TOOL_TEMPERATURE', m.names())
        m.printer['extruder'].update(temperature=220, target=220, can_extrude=True)
        m.run('T0')
        self.assertEqual([(1, -20)], extrusion_moves(m))

    def test_sparse_physical_tool_numbers_keep_independent_repayment(self):
        m = Machine(3)
        m.tc.update(tool_numbers=[0, 2], tool_names=['tool T0', 'tool T2'])
        m.run('T2\nT0')
        self.assertEqual([(0, -20), (2, -20), (0, 20)], extrusion_moves(m))

    def test_outgoing_retraction_requires_confirmed_selected_extruder(self):
        for fault in ['detection', 'extruder', 'source', 'initializing', 'failed']:
            with self.subTest(fault=fault):
                m = Machine()
                m.tc['status'] = 'changing'
                source = 0
                if fault == 'detection':
                    m.tc['detected_tool_number'] = 1
                elif fault == 'extruder':
                    m.printer['toolhead']['extruder'] = 'extruder1'
                elif fault == 'source':
                    source = 1
                elif fault == 'initializing':
                    m.tc['status'] = 'initializing'
                else:
                    m.state['failed'] = True
                try:
                    m.run(f'_KLIPPAIN_TOOLCHANGE_BEFORE FROM={source} TO=1')
                except MacroError:
                    pass
                self.assertEqual([], extrusion_moves(m))

    def test_existing_debt_is_only_topped_up_to_current_configured_length(self):
        m = Machine()
        m.tc['status'] = 'changing'
        m.run('_KLIPPAIN_TOOLCHANGE_BEFORE FROM=0 TO=1')
        m.run('_KLIPPAIN_TOOLCHANGE_BEFORE FROM=0 TO=1')
        m.vars['retract_length'] = 30
        m.run('_KLIPPAIN_TOOLCHANGE_BEFORE FROM=0 TO=1')
        m.vars['retract_length'] = 5
        m.run('_KLIPPAIN_TOOLCHANGE_BEFORE FROM=0 TO=1')
        self.assertEqual([(0, -20), (0, -10)], extrusion_moves(m))
        self.assertEqual({0: 30}, m.state['retract_debts'])

    def test_failed_retraction_does_not_create_debt(self):
        for command in ['G1', 'M400']:
            with self.subTest(command=command):
                m = Machine()
                def fail(machine, params):
                    raise MacroError('injected primitive failure')
                m.command_hooks[command] = fail
                with self.assertRaisesRegex(MacroError, 'injected primitive failure'):
                    m.run('T1')
                self.assertEqual({}, m.state['retract_debts'])

    def test_failed_restore_retains_debt_for_manual_recovery(self):
        for command in ['G1', 'M400']:
            with self.subTest(command=command):
                m = Machine()
                m.state['retract_debts'] = {0: 17.5, 1: 9}
                def fail(machine, params):
                    raise MacroError('injected primitive failure')
                m.command_hooks[command] = fail
                with self.assertRaisesRegex(MacroError, 'injected primitive failure'):
                    m.run('_KLIPPAIN_TOOL_RESTORE T=0 REQUIRE=1')
                self.assertEqual({0: 17.5, 1: 9}, m.state['retract_debts'])

    def test_restore_guard_keeps_debt_without_moving_or_heating(self):
        for fault in ['cold', 'target_off', 'probe_target', 'detection', 'extruder',
                      'selected', 'changing', 'initializing', 'failed']:
            with self.subTest(fault=fault):
                m = Machine()
                m.state['retract_debts'] = {0: 12.5}
                if fault == 'cold':
                    m.printer['extruder']['can_extrude'] = False
                elif fault == 'target_off':
                    m.printer['extruder']['target'] = 0
                elif fault == 'probe_target':
                    m.printer['extruder']['target'] = 150
                elif fault == 'detection':
                    m.tc['detected_tool_number'] = 1
                elif fault == 'extruder':
                    m.printer['toolhead']['extruder'] = 'extruder1'
                elif fault == 'selected':
                    m.set_active(1)
                elif fault in ['changing', 'initializing']:
                    m.tc['status'] = fault
                else:
                    m.state['failed'] = True
                m.run('_KLIPPAIN_TOOL_RESTORE T=0')
                with self.assertRaises(MacroError):
                    m.run('_KLIPPAIN_TOOL_RESTORE T=0 REQUIRE=1')
                self.assertEqual([], extrusion_moves(m))
                self.assertEqual({0: 12.5}, m.state['retract_debts'])
                self.assertNotIn('SET_TOOL_TEMPERATURE', m.names())

    def test_cold_return_repaid_by_active_m104_before_next_extrusion(self):
        m = Machine()
        m.run('T1')
        m.printer['extruder'].update(temperature=35, target=0, can_extrude=False)
        m.run('T0')
        self.assertEqual([(0, -20), (1, -20)], extrusion_moves(m))
        self.assertEqual({0: 20, 1: 20}, m.state['retract_debts'])
        m.run('M104 T0 S220\nG1 E1 F300\nM104 T0 S220')
        self.assertEqual([(0, -20), (1, -20), (0, 20), (0, 1)], extrusion_moves(m))
        self.assertEqual({1: 20}, m.state['retract_debts'])
        self.assertEqual(1, m.names().count('M109'))

    def test_parked_m104_stays_async_and_parked_m109_does_not_repay(self):
        m = Machine()
        m.state['retract_debts'] = {1: 16.25}
        m.run('M104 T1 S235')
        self.assertNotIn('M109', m.names())
        self.assertFalse(any(name == 'SET_TOOL_TEMPERATURE' and p.get('WAIT') == '1'
                             for name, p in m.commands))
        m.run('M109 T1 S235')
        self.assertEqual([], extrusion_moves(m))
        self.assertEqual({1: 16.25}, m.state['retract_debts'])
        m.run('T1')
        self.assertEqual([(0, -20), (1, 16.25)], extrusion_moves(m))

    def test_sparse_m104_rejects_extruder_index_that_is_not_a_physical_tool(self):
        m = Machine(3)
        m.tc.update(tool_numbers=[0, 2], tool_names=['tool T0', 'tool T2'])
        before = {name: m.printer[name]['target'] for name in ['extruder', 'extruder1', 'extruder2']}
        with self.assertRaisesRegex(MacroError, 'unconfigured tool'):
            m.run('M104 T1 S200')
        self.assertEqual(before, {name: m.printer[name]['target'] for name in before})
        m.run('M104 T2 S245')
        self.assertEqual(245, m.printer['extruder2']['target'])
        self.assertEqual(before['extruder1'], m.printer['extruder1']['target'])

    def test_cold_return_and_later_departure_do_not_retract_twice(self):
        m = Machine()
        m.run('T1')
        m.printer['extruder'].update(temperature=35, target=0, can_extrude=False)
        m.run('T0')
        m.printer['extruder'].update(temperature=200, target=150, can_extrude=True)
        m.run('T1')
        self.assertEqual([(0, -20), (1, -20), (1, 20)], extrusion_moves(m))
        self.assertEqual({0: 20}, m.state['retract_debts'])

    def test_m109_both_wait_paths_repay_after_wait_with_fresh_detection(self):
        for fix in [False, True]:
            for lose_detection in [False, True]:
                with self.subTest(fix=fix, lose_detection=lose_detection):
                    m = Machine()
                    m.vars['fix_heaters_temperature_settle'] = fix
                    m.state['retract_debts'] = {0: 20}
                    m.printer['extruder'].update(temperature=30, target=0, can_extrude=False)
                    waiter = 'TEMPERATURE_WAIT' if fix else 'SET_TOOL_TEMPERATURE'
                    if lose_detection:
                        def lose(machine, params):
                            machine.tc['detected_tool_number'] = -1
                        m.command_hooks[waiter] = lose
                        with self.assertRaises(MacroError):
                            m.run('M109 T0 S220')
                        self.assertEqual([], extrusion_moves(m))
                        self.assertEqual({0: 20}, m.state['retract_debts'])
                    else:
                        m.run('M109 T0 S220')
                        self.assertEqual([(0, 20)], extrusion_moves(m))
                        self.assertLess(m.names().index(waiter), m.names().index('G1'))

    def test_low_or_opted_out_temperature_commands_do_not_repay(self):
        m = Machine()
        m.state['retract_debts'] = {0: 20}
        m.run('M104 S0\nM104 S150\nM109 S150\nM104 S220 RESTORE=0\nM109 S220 RESTORE=0')
        self.assertEqual([], extrusion_moves(m))
        self.assertEqual({0: 20}, m.state['retract_debts'])

    def test_preheat_keeps_debt_and_bulk_targets_async_even_at_low_minimum(self):
        for preheat in [False, True]:
            with self.subTest(preheat=preheat):
                m = Machine(3)
                m.vars['toolchanger_preheat_used_tools'] = preheat
                m.state['retract_debts'] = {0: 20, 1: 15}
                m.printer['configfile']['config']['extruder']['min_extrude_temp'] = 100
                m.printer['gcode_macro START_PRINT']['tool_temperatures'] = {0: 220, 1: 230}
                m.run('_MODULE_EXTRUDER_PREHEATING')
                self.assertEqual([], extrusion_moves(m))
                self.assertEqual({0: 20, 1: 15}, m.state['retract_debts'])
                self.assertFalse(any(name == 'SET_TOOL_TEMPERATURE' and p.get('WAIT') == '1'
                                     and p.get('T') != '0' for name, p in m.commands))

    def test_probe_guard_and_restore_saved_temperature_never_repay(self):
        m = Machine()
        m.state['retract_debts'] = {0: 20}
        m.printer['configfile']['config']['extruder']['min_extrude_temp'] = 100
        m.run('_PROBE_ENTER_CONTACT_GUARD\n_PROBE_EXIT_CONTACT_GUARD')
        self.assertEqual([], extrusion_moves(m))
        self.assertEqual({0: 20}, m.state['retract_debts'])

    def test_manual_failed_change_resume_restores_target_once_before_native_resume(self):
        m = Machine()
        m.run('T1')
        m.fail_next = True
        m.run('T0')
        self.assertTrue(m.state['failed'])
        self.assertEqual({0: 20, 1: 20}, m.state['retract_debts'])
        self.assertEqual([(0, -20), (1, -20)], extrusion_moves(m))
        selections = m.names().count('SELECT_TOOL')
        m.tc['detected_tool_number'] = 0
        m.run('RESUME')
        self.assertEqual(selections, m.names().count('SELECT_TOOL'))
        self.assertEqual([(0, -20), (1, -20), (0, 20)], extrusion_moves(m))
        self.assertEqual({1: 20}, m.state['retract_debts'])
        self.assertEqual(1, m.native_resumes)
        self.assertIn('PAUSE_STATE', m.saved)
        self.assertNotIn('PAUSE_state', m.saved)
        self.assertLess(max(i for i, (name, _) in enumerate(m.commands) if name == 'G1'),
                        m.names().index('BASE_RESUME'))

    def test_pause_captures_slicer_e_and_restores_it_after_native_resume(self):
        for failed_change in [False, True]:
            with self.subTest(failed_change=failed_change):
                m = Machine()
                m.run('G92 E123.4')
                if failed_change:
                    m.fail_next = True
                    m.run('T1')
                    m.tc['detected_tool_number'] = 1
                else:
                    m.run('PAUSE PARK=0')
                self.assertEqual(123.4, m.state['paused_e'])
                m.run('RESUME')
                resume = m.names().index('BASE_RESUME')
                name, params = m.commands[resume + 1]
                self.assertEqual('G92', name)
                self.assertEqual(123.4, float(params['E']))

    def test_failed_exchange_repairs_snapshot_before_return_and_forwards_velocity(self):
        for velocity in [None, 23.5]:
            with self.subTest(velocity=velocity):
                m = Machine()
                m.printer['configfile']['settings']['pause_resume']['recover_velocity'] = 37
                m.state['retract_debts'] = {1: 7.5}
                m.fail_next = True
                m.run('T1')
                self.assertTrue(m.state['change_recovery'])
                m.tc['detected_tool_number'] = 1
                boundary = len(m.commands)
                m.run('RESUME' + (f' VELOCITY={velocity}' if velocity is not None else ''))
                commands = m.commands[boundary:]
                repairs = [(i, params) for i, (name, params) in enumerate(commands)
                           if name == 'RESTORE_GCODE_STATE' and params.get('NAME') == '_toolchange_state'
                           and params.get('MOVE') == '1']
                self.assertEqual(1, len(repairs))
                repair, params = repairs[0]
                self.assertEqual(37 if velocity is None else velocity, float(params['MOVE_SPEED']))
                repay = next(i for i, (name, params) in enumerate(commands)
                             if name == 'G1' and float(params.get('E', 0)) > 0)
                refresh = next(i for i, (name, params) in enumerate(commands)
                               if name == 'SAVE_GCODE_STATE' and params['NAME'] == 'PAUSE_STATE')
                resume = next(i for i, (name, _) in enumerate(commands) if name == 'BASE_RESUME')
                self.assertLess(repair, repay)
                self.assertLess(repay, refresh)
                self.assertLess(refresh, resume)
                if velocity is not None:
                    self.assertEqual(velocity, float(commands[resume][1]['VELOCITY']))
                self.assertFalse(m.state['change_recovery'])
                self.assertEqual({0: 20}, m.state['retract_debts'])

    def test_ordinary_and_detection_only_pause_do_not_reuse_stale_exchange_snapshot(self):
        for error in [False, True]:
            with self.subTest(error=error):
                m = Machine()
                m.run('T1\nG1 X100 F1800')
                self.assertIn('_toolchange_state', m.saved)
                if error:
                    m.run('_KLIPPAIN_TOOLCHANGE_ERROR TARGET=1 CHANGING=0')
                else:
                    m.state['change_recovery'] = True
                    m.run('PAUSE PARK=0')
                self.assertFalse(m.state['change_recovery'])
                saved_position = copy.deepcopy(m.saved['PAUSE_STATE'][1])
                m.tc['detected_tool_number'] = 1
                boundary = len(m.commands)
                m.run('RESUME')
                self.assertFalse(any(name == 'RESTORE_GCODE_STATE' and params.get('NAME') == '_toolchange_state'
                                     and params.get('MOVE') == '1' for name, params in m.commands[boundary:]))
                self.assertEqual(saved_position, m.printer['toolhead']['position'])

    def test_secondary_detection_error_preserves_pending_exchange_recovery(self):
        m = Machine()
        m.fail_next = True
        m.run('T1')
        state = copy.deepcopy(m.saved['_toolchange_state'])
        m.run('_KLIPPAIN_TOOLCHANGE_ERROR TARGET=1 CHANGING=0')
        self.assertTrue(m.state['change_recovery'])
        self.assertEqual(state, m.saved['_toolchange_state'])
        self.assertEqual(1, m.native_pauses)

    def test_recovery_rechecks_after_heating_before_any_position_repair(self):
        for fault in ['homed', 'detection', 'selected', 'extruder', 'status', 'failed']:
            with self.subTest(fault=fault):
                m = Machine()
                m.state['retract_debts'] = {1: 7.5}
                m.fail_next = True
                m.run('T1')
                m.tc['detected_tool_number'] = 1
                def lose_state(machine, params):
                    if params.get('WAIT') != '1':
                        return
                    if fault == 'homed':
                        machine.printer['toolhead']['homed_axes'] = 'xy'
                    elif fault == 'detection':
                        machine.tc['detected_tool_number'] = -1
                    elif fault == 'selected':
                        machine.tc['tool_number'] = 0
                    elif fault == 'extruder':
                        machine.printer['toolhead']['extruder'] = 'extruder'
                    elif fault == 'status':
                        machine.tc['status'] = 'uninitialized'
                    else:
                        machine.state['failed'] = True
                m.command_hooks['SET_TOOL_TEMPERATURE'] = lose_state
                boundary = len(m.commands)
                with self.assertRaisesRegex(MacroError, 'lost its confirmed'):
                    m.run('RESUME')
                self.assertFalse(any(name == 'RESTORE_GCODE_STATE' and params.get('MOVE') == '1'
                                     for name, params in m.commands[boundary:]))
                self.assertEqual({0: 20, 1: 7.5}, m.state['retract_debts'])
                self.assertTrue(m.state['change_recovery'])
                self.assertEqual(0, m.native_resumes)

    def test_retry_after_failed_reheat_preserves_original_exchange_snapshot(self):
        m = Machine()
        m.fail_next = True
        m.run('T1')
        saved = copy.deepcopy(m.saved['_toolchange_state'])
        m.tc['detected_tool_number'] = 1
        def lose_detection(machine, params):
            if params.get('WAIT') == '1':
                machine.tc['detected_tool_number'] = -1
        m.command_hooks['SET_TOOL_TEMPERATURE'] = lose_detection
        with self.assertRaises(MacroError):
            m.run('RESUME')
        self.assertEqual(1, m.state['paused_tool'])
        self.assertTrue(m.state['change_recovery'])
        del m.command_hooks['SET_TOOL_TEMPERATURE']
        m.tc['detected_tool_number'] = 1
        m.run('RESUME')
        self.assertEqual(saved, m.saved['_toolchange_state'])
        self.assertFalse(m.state['change_recovery'])
        self.assertEqual(1, m.native_resumes)

    def test_successful_end_or_cancel_clears_pending_exchange_repair(self):
        for command in ['END_PRINT', 'CANCEL_PRINT']:
            with self.subTest(command=command):
                m = Machine()
                m.fail_next = True
                m.run('T1')
                self.assertTrue(m.state['change_recovery'])
                m.tc['detected_tool_number'] = 0
                m.run(command)
                self.assertFalse(m.state['change_recovery'])

    def test_end_and_cancel_keep_swap_debt_and_existing_end_retraction(self):
        for command in ['END_PRINT', 'CANCEL_PRINT']:
            with self.subTest(command=command):
                m = Machine()
                m.run('T1')
                m.run(command)
                self.assertEqual({0: 20, 1: 20}, m.state['retract_debts'])
                self.assertFalse(any(value > 0 for _, value in extrusion_moves(m)))
                self.assertEqual(1, sum(tool == 0 and value == -20
                                        for tool, value in extrusion_moves(m)[1:]))

    def test_start_reset_keeps_unselected_debt_for_later_use(self):
        m = Machine()
        m.state['retract_debts'] = {1: 13.5}
        m.vars.update(force_homing_in_start_print=False, verbose=False,
                      startprint_actions=('extruder_heating',))
        m.run('START_PRINT MATERIAL=PLA SOAK=0 INITIAL_TOOL=0 T0_TEMP=220 T1_TEMP=230')
        self.assertEqual({1: 13.5}, m.state['retract_debts'])
        m.run('T1\nM109 T1 S230')
        self.assertEqual(1, extrusion_moves(m).count((1, 13.5)))

    def test_both_start_prime_modes_with_preheat_off_preserve_per_tool_debt(self):
        for prime_all in [False, True]:
            with self.subTest(prime_all=prime_all):
                m = Machine(3)
                m.vars.update(toolchanger_prime_all_used_tools=prime_all,
                              toolchanger_preheat_used_tools=False,
                              force_homing_in_start_print=False, verbose=False,
                              startprint_actions=('extruder_preheating', 'extruder_heating', 'primeline'))
                m.state['retract_debts'] = {0: 20, 1: 5.5, 2: 12}
                for heater in ['extruder', 'extruder1', 'extruder2']:
                    m.printer[heater].update(temperature=35, target=0, can_extrude=False)
                m.run('START_PRINT MATERIAL=PLA SOAK=0 INITIAL_TOOL=1 T0_TEMP=220 T1_TEMP=230 T2_TEMP=240')
                self.assertEqual(1, m.tc['tool_number'])
                self.assertEqual([0, 1, 2] if prime_all else [1],
                                 sorted(m.printer['gcode_macro START_PRINT']['primed_tools']))
                self.assertEqual({0: 20, 2: 20 if prime_all else 12}, m.state['retract_debts'])
                presses = [move for move in m.moves if float(move['params'].get('E', 0)) == 23]
                self.assertEqual(3 if prime_all else 1, len(presses))
                self.assertEqual(1, extrusion_moves(m).count((1, 5.5)))
                self.assertEqual(1 if prime_all else 0, extrusion_moves(m).count((2, 12)))

    def test_primeline_restores_swap_debt_once_and_keeps_canonical_press(self):
        m = Machine()
        m.state['retract_debts'] = {0: 20}
        m.run('PRIMELINE ADAPTIVE_MODE=0')
        positive_e_only = [(move['tool'], float(move['params']['E']))
                           for move in m.moves if float(move['params'].get('E', 0)) > 0
                           and not any(axis in move['params'] for axis in 'XYZ')]
        self.assertEqual([(0, 20), (0, 23)], positive_e_only)
        self.assertEqual({}, m.state['retract_debts'])

    def test_real_purge_repays_once_without_tracking_its_existing_retract(self):
        m = Machine()
        load_real_purge(m)
        m.state['retract_debts'] = {0: 20}
        m.run('PURGE DISTANCE=40 OOZE_TIME=0\nPURGE DISTANCE=40 OOZE_TIME=0')
        self.assertEqual([(0, 20), (0, 40), (0, -2), (0, -18),
                          (0, 40), (0, -2), (0, -18)], extrusion_moves(m))
        self.assertEqual({}, m.state['retract_debts'])

    def test_toolchange_cleanup_and_real_purge_do_not_repay_twice(self):
        m = Machine()
        load_real_purge(m)
        m.vars.update(toolchanger_purge_on_change=True, purge_distance=40,
                      purge_ooze_time=0)
        m.state['retract_debts'] = {1: 20}
        m.run('T1\nT1')
        self.assertEqual([(0, -20), (1, 20), (1, 40), (1, -2), (1, -18)], extrusion_moves(m))
        self.assertEqual({0: 20}, m.state['retract_debts'])


if __name__ == '__main__':
    unittest.main()
