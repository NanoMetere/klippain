from __future__ import annotations

import re
import unittest

from toolchanger_harness import Machine, MacroError, config


STATES = {
    'ready': ('standby', 'standby'), 'busy': ('busy', 'on'),
    'heating': ('heating', 'heating'), 'leveling': ('leveling', 'on'),
    'homing': ('homing', 'on'), 'cleaning': ('cleaning', 'on'),
    'meshing': ('meshing', 'on'), 'calibrating_z': ('calibrating_z', 'on'),
    'printing': ('printing', 'on'), 'done_printing': ('done_printing', 'done_printing'),
    'on': ('on', 'on'), 'off': ('off', 'off'), 'error': ('error', 'error'),
    'shutdown': ('off', 'off'),
}


def effects(machine):
    return [params for name, params in machine.commands if name == 'SET_LED_EFFECT']


class ToolLedProfilesTest(unittest.TestCase):
    def test_every_effect_preserves_legacy_layers_and_lifecycle(self):
        legacy = config('config/hardware/lights/status_leds_effects.cfg')
        normalize = lambda value: re.sub(r'\s+', '', value)
        for number in range(7):
            profile = config(f'config/hardware/lights/toolchanger/T{number}.cfg')
            self.assertEqual(21, len(profile.sections()))
            for section in legacy.sections():
                if not section.startswith('led_effect '):
                    continue
                suffix = section.removeprefix('led_effect ').removeprefix('sb_')
                target = f'led_effect tc_t{number}_{suffix}'
                with self.subTest(tool=number, effect=suffix):
                    self.assertEqual(normalize(legacy.get(section, 'layers')),
                                     normalize(profile.get(target, 'layers')))
                    for option, default in [('autostart', 'false'), ('frame_rate', '24'),
                                            ('run_on_error', 'false')]:
                        self.assertEqual(legacy.get(section, option, fallback=default).lower(),
                                         profile.get(target, option, fallback=default).lower())
                    selector = profile.get(target, 'leds').strip()
                    expected = f'neopixel:T{number}_leds'
                    if suffix.startswith('logo_'):
                        expected += ' (1)'
                    elif suffix.startswith('nozzle_'):
                        expected += ' (2,3)'
                    self.assertEqual(expected, selector)

    def test_shared_caselight_off_matches_full_chain_scope(self):
        profile = config('config/hardware/lights/neopixel_caselight_effects.cfg')
        self.assertEqual('neopixel:caselight', profile.get('led_effect cl_off', 'leds').strip())


class ToolLedCommandsTest(unittest.TestCase):
    def test_invalid_tool_tokens_reject_before_any_led_command(self):
        for command in ['SET_LOGO_LEDS_OFF', 'SET_NOZZLE_LEDS_ON',
                        'SET_NOZZLE_LEDS_OFF', '_SET_LOGO_LEDS', '_SET_NOZZLE_LEDS']:
            for token in ['banana', '2.5', '-1', '7', '1e0', '']:
                with self.subTest(command=command, token=token):
                    machine = Machine(3)
                    with self.assertRaises(MacroError):
                        machine.run(f'{command} T="{token}"')
                    self.assertEqual([], effects(machine))
                    self.assertNotIn('SET_LED', machine.names())
                    self.assertNotIn('STOP_LED_EFFECTS', machine.names())
                    self.assertNotIn('SELECT_TOOL', machine.names())

    def test_status_mapping_for_active_parked_and_broadcast_tools(self):
        for state, active_states in STATES.items():
            with self.subTest(state=state):
                machine = Machine(3)
                machine.set_active(1)
                machine.run(f'STATUS_LEDS COLOR={state}')
                expected = set()
                for number in range(3):
                    states = active_states if number == 1 or state in ['error', 'off', 'shutdown'] else ('standby', 'standby')
                    for group, suffix in zip(['logo', 'nozzle'], states):
                        expected.add(f'tc_t{number}_{group}_{suffix}')
                self.assertEqual(expected, {p['EFFECT'] for p in effects(machine)})
                self.assertTrue(all(p.get('REPLACE') == '1' and p.get('FADETIME') == '0.5'
                                    for p in effects(machine)))
                self.assertNotIn('SET_LED', machine.names())

    def test_last_status_survives_tool_activation(self):
        machine = Machine(3)
        machine.run('STATUS_LEDS COLOR=printing')
        machine.commands.clear()
        machine.run('T2')
        self.assertEqual({'tc_t0_logo_standby', 'tc_t0_nozzle_standby',
                          'tc_t1_logo_standby', 'tc_t1_nozzle_standby',
                          'tc_t2_logo_printing', 'tc_t2_nozzle_on'},
                         {p['EFFECT'] for p in effects(machine)})

    def test_public_group_commands_default_selected_or_honor_explicit_tool(self):
        commands = [('SET_LOGO_LEDS_OFF', 'logo_off'),
                    ('SET_NOZZLE_LEDS_ON', 'nozzle_on'),
                    ('SET_NOZZLE_LEDS_OFF', 'nozzle_off')]
        for command, suffix in commands:
            for explicit in [False, True]:
                with self.subTest(command=command, explicit=explicit):
                    machine = Machine(3)
                    machine.set_active(1)
                    machine.run(command + (' T=2' if explicit else ''))
                    self.assertEqual([f'tc_t{2 if explicit else 1}_{suffix}'],
                                     [p['EFFECT'] for p in effects(machine)])

    def test_raw_writes_stop_only_the_selected_tool_group_pixels(self):
        for command, indexes in [('_SET_LOGO_LEDS', [1]), ('_SET_NOZZLE_LEDS', [2, 3])]:
            with self.subTest(command=command):
                machine = Machine(3)
                machine.run(f'{command} T=2 RED=0.25 BLUE=0.75')
                stops = [p['LEDS'] for n, p in machine.commands if n == 'STOP_LED_EFFECTS']
                self.assertEqual([f'neopixel:T2_leds ({",".join(map(str, indexes))})'], stops)
                writes = [p for n, p in machine.commands if n == 'SET_LED']
                self.assertEqual(indexes, [int(p['INDEX']) for p in writes])
                self.assertTrue(all(p['LED'] == 'T2_leds' and float(p['RED']) == 0.25
                                    and float(p['BLUE']) == 0.75 for p in writes))
                self.assertEqual('1', writes[-1]['TRANSMIT'])
                self.assertTrue(all(p['TRANSMIT'] == '0' for p in writes[:-1]))

    def test_led_disabled_status_and_startup_skip_tool_effects(self):
        machine = Machine()
        machine.vars['toolchanger_leds_enabled'] = False
        machine.run('STATUS_LEDS COLOR=printing\n_INIT_LEDS\nT1')
        self.assertEqual([], effects(machine))
        self.assertNotIn('SET_LED', machine.names())

    def test_startup_preference_selects_ready_or_off_for_all_tools(self):
        for enabled in [False, True]:
            with self.subTest(enabled=enabled):
                machine = Machine(3)
                machine.vars['caselight_on_at_startup'] = enabled
                machine.run('_INIT_LEDS')
                suffix = 'standby' if enabled else 'off'
                self.assertEqual({f'tc_t{number}_{group}_{suffix}' for number in range(3)
                                  for group in ['logo', 'nozzle']},
                                 {p['EFFECT'] for p in effects(machine)})

    def test_missing_effect_or_duplicate_prefix_fails_before_sensor_changes(self):
        for defect in ['missing', 'duplicate']:
            with self.subTest(defect=defect):
                machine = Machine()
                if defect == 'missing':
                    del machine.printer['led_effect tc_t1_nozzle_off']
                else:
                    machine.printer['tool T1']['params_led_effect_prefix'] = 'tc_t0'
                with self.assertRaises(MacroError):
                    machine.run('_INIT_CHECK_TOOLCHANGER')
                self.assertNotIn('SET_FILAMENT_SENSOR', machine.names())

    def test_shared_static_caselight_stays_independent_of_tool_effects(self):
        machine = Machine()
        machine.vars.update(status_leds_caselight_enabled=True,
                            status_leds_caselight_led_name='caselight',
                            status_leds_effects_enabled=False)
        machine.run('STATUS_LEDS COLOR=printing')
        self.assertEqual(4, len(effects(machine)))
        self.assertEqual(['neopixel:caselight'],
                         [p['LEDS'] for n, p in machine.commands if n == 'STOP_LED_EFFECTS'])
        self.assertEqual(['caselight'], [p['LED'] for n, p in machine.commands if n == 'SET_LED'])

    def test_legacy_single_tool_status_effects_keep_existing_names(self):
        machine = Machine()
        machine.vars.update(klippain_toolchanger_enabled=False, status_leds_enabled=True,
                            status_leds_effects_enabled=True)
        machine.run('STATUS_LEDS COLOR=printing')
        self.assertEqual(['sb_logo_printing', 'sb_nozzle_on'],
                         [p['EFFECT'] for p in effects(machine)])


class ToolLedTopologyTest(unittest.TestCase):
    @staticmethod
    def shared_chain():
        machine = Machine()
        machine.printer['neopixel shared'] = dict(color_data=[(0., 0., 0., 0.)] * 6)
        settings = machine.printer['configfile']['config']
        settings['neopixel shared'] = dict(chain_count=6)
        for number in range(2):
            base = number * 3
            machine.printer[f'tool T{number}'].update(
                params_led_name='shared', params_led_logo_idx=str(base + 1),
                params_led_nozzle_idx=f'{base + 2}, {base + 3}')
            for name in settings:
                if name.startswith(f'led_effect tc_t{number}_'):
                    group = name.removeprefix(f'led_effect tc_t{number}_').split('_')[0]
                    indexes = str(base + 1) if group == 'logo' else (
                        f'{base + 3}-{base + 2}' if group == 'nozzle' else f'{base + 1}-{base + 3}')
                    settings[name]['leds'] = f'neopixel:shared ({indexes})'
        return machine

    def test_disjoint_shared_chain_and_descending_selectors_pass(self):
        machine = self.shared_chain()
        machine.run('_INIT_CHECK_TOOLCHANGER\n_SET_NOZZLE_LEDS T=1 RED=0.5')
        self.assertEqual(['neopixel:shared(5,6)'],
                         [p['LEDS'].replace(' ', '') for n, p in machine.commands if n == 'STOP_LED_EFFECTS'])
        self.assertEqual([5, 6], [int(p['INDEX']) for n, p in machine.commands if n == 'SET_LED'])

    def test_mismatched_chain_or_group_metadata_rejects_before_sensors(self):
        changes = [('params_led_name', 'T1_leds'), ('params_led_logo_idx', '1.5'),
                   ('params_led_logo_idx', '4'), ('params_led_logo_idx', '1,1'),
                   ('params_led_nozzle_idx', '1,2')]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                machine = Machine()
                machine.printer['tool T0'][key] = value
                with self.assertRaises(MacroError):
                    machine.run('_INIT_CHECK_TOOLCHANGER')
                self.assertNotIn('SET_FILAMENT_SENSOR', machine.names())

    def test_wrong_effect_chain_indices_and_full_chain_shared_scope_reject(self):
        changes = [('logo_printing', 'neopixel:T1_leds (1)'),
                   ('nozzle_on', 'neopixel:T0_leds (2,4)'),
                   ('logo_off', 'neopixel:T0_leds (2)'),
                   ('startup', 'neopixel:shared'),
                   ('critical_error', 'neopixel:shared (1-6)')]
        for suffix, selector in changes:
            with self.subTest(suffix=suffix, selector=selector):
                machine = self.shared_chain() if 'shared' in selector else Machine()
                machine.printer['configfile']['config'][f'led_effect tc_t0_{suffix}']['leds'] = selector
                with self.assertRaises(MacroError):
                    machine.run('_INIT_CHECK_TOOLCHANGER')
                self.assertNotIn('SET_FILAMENT_SENSOR', machine.names())

    def test_shared_chain_overlapping_tool_ownership_rejects(self):
        machine = self.shared_chain()
        machine.printer['tool T1']['params_led_logo_idx'] = '3'
        with self.assertRaisesRegex(MacroError, 'shares LED pixel'):
            machine.run('_INIT_CHECK_TOOLCHANGER')
        self.assertNotIn('SET_FILAMENT_SENSOR', machine.names())


if __name__ == '__main__':
    unittest.main()
