from __future__ import annotations

import unittest
from pathlib import Path

from toolchanger_harness import Machine, MacroError, ROOT, config


class ToolchangerTest(unittest.TestCase):
    def test_two_to_seven_tools_both_printer_sizes(self):
        for size in [300,350]:
            for count in range(2,8):
                with self.subTest(size=size,count=count):
                    m=Machine(count,size)
                    m.run('_INIT_CHECK_TOOLCHANGER')
                    m.run(f'T{count-1}')
                    self.assertEqual(count-1,m.tc['tool_number'])
                    self.assertFalse(m.state['failed'])
                    m.run('_KLIPPAIN_TOOLCHANGER_END')
                    self.assertEqual(0,m.tc['tool_number'])

    def test_invalid_tool_does_not_move(self):
        m=Machine()
        with self.assertRaisesRegex(MacroError,'not configured'):
            m.run('T6')
        self.assertNotIn('SELECT_TOOL',m.names())

    def test_uncalibrated_dock_does_not_move(self):
        m=Machine()
        m.printer['tool T1']['params_docks_calibrated']=False
        with self.assertRaisesRegex(MacroError,'Calibrate every dock'):
            m.run('T1')
        self.assertNotIn('SELECT_TOOL',m.names())

    def test_nonconsecutive_tool_numbers(self):
        m=Machine(3)
        m.tc.update(tool_numbers=[0,2],tool_names=['tool T0','tool T2'])
        m.run('_INIT_CHECK_TOOLCHANGER\nM109 T2 S245\nT2')
        self.assertEqual(2,m.tc['tool_number'])
        self.assertEqual(245,m.printer['extruder2']['target'])

    def test_shared_tool_sensor_is_rejected(self):
        m=Machine()
        m.printer['tool T1']['params_filament_sensors']=['T0_runout']
        with self.assertRaisesRegex(MacroError,'must not be shared'):
            m.run('_INIT_CHECK_TOOLCHANGER')

    def test_happy_hare_is_explicitly_rejected(self):
        m=Machine()
        m.vars['klippain_mmu_enabled']=True
        with self.assertRaisesRegex(MacroError,'cannot be enabled together'):
            m.run('_INIT_CHECK_TOOLCHANGER')

    def test_probing_actions_after_heating_rejected(self):
        m=Machine()
        m.vars['startprint_actions']=('extruder_heating','bedmesh')
        with self.assertRaisesRegex(MacroError,'before extruder_heating'):
            m.run('_INIT_CHECK_TOOLCHANGER')

    def test_all_manual_probing_selects_t0(self):
        for command in ['PROBE','PROBE_ACCURACY','PROBE_CALIBRATE','QUAD_GANTRY_LEVEL','Z_TILT_ADJUST','BED_MESH_CALIBRATE']:
            with self.subTest(command=command):
                m=Machine()
                m.set_active(1)
                m.run(command)
                self.assertEqual(0,m.tc['tool_number'])
                self.assertLess(m.names().index('SELECT_TOOL'),m.names().index('_PROBE_ENTER_CONTACT_GUARD'))

    def test_cold_home_requires_t0_by_default(self):
        m=Machine()
        m.set_active(1)
        m.printer['toolhead']['homed_axes']=''
        with self.assertRaisesRegex(MacroError,'manually mount T0'):
            m.run('G28')
        self.assertNotIn('SELECT_TOOL',m.names())
        self.assertNotIn('G0',m.names())

    def test_cold_home_with_mounted_tool_then_t0_rehomes_z(self):
        m=Machine()
        m.set_active(1)
        m.printer['toolhead']['homed_axes']=''
        m.vars['toolchanger_home_with_any_tool']=True
        m.run('G28')
        zhomes=[i for i,(name,p) in enumerate(m.commands) if name=='G28' and 'Z' in p]
        select=m.names().index('SELECT_TOOL')
        self.assertEqual(2,len(zhomes))
        self.assertLess(zhomes[0],select)
        self.assertLess(select,zhomes[1])
        self.assertEqual(0,m.tc['tool_number'])
        self.assertFalse(m.state['bootstrap_homing'])

    def test_first_home_option_does_not_enable_non_t0_manual_probing(self):
        m=Machine()
        m.set_active(1)
        m.vars['toolchanger_home_with_any_tool']=True
        with self.assertRaisesRegex(MacroError,'Probing requires'):
            m.run('_KLIPPAIN_ASSERT_PROBE_TOOL')

    def test_manual_probe_clears_stale_bootstrap_after_interrupted_home(self):
        m=Machine()
        m.set_active(1)
        m.vars['toolchanger_home_with_any_tool']=True
        m.state['bootstrap_homing']=True
        m.run('PROBE')
        self.assertFalse(m.state['bootstrap_homing'])
        self.assertEqual(0,m.tc['tool_number'])

    def test_initial_tool_heated_after_selection(self):
        m=Machine()
        m.printer['gcode_macro START_PRINT'].update(initial_tool=1,extruder_temp=235)
        m.run('_MODULE_EXTRUDER_HEATING')
        self.assertLess(m.names().index('SELECT_TOOL'),m.names().index('M109'))
        self.assertEqual(235,m.printer['extruder1']['target'])
        self.assertEqual(220,m.printer['extruder']['target'])

    def test_temperature_commands_route_explicit_tool(self):
        m=Machine()
        m.run('M104 T1 S245')
        m.run('M109 T1 S250')
        self.assertEqual(250,m.printer['extruder1']['target'])
        self.assertEqual(220,m.printer['extruder']['target'])

    def test_heater_workaround_waits_on_target_tool(self):
        m=Machine()
        m.vars['fix_heaters_temperature_settle']=True
        m.run('M109 T1 S250')
        waits=[p for n,p in m.commands if n=='TEMPERATURE_WAIT']
        self.assertEqual([dict(SENSOR='extruder1',MINIMUM='250.0')],waits)

    def test_end_and_cancel_t0_precedes_park(self):
        for command in ['END_PRINT','CANCEL_PRINT']:
            with self.subTest(command=command):
                m=Machine()
                m.set_active(1)
                m.run(command)
                self.assertLess(m.names().index('SELECT_TOOL'),m.names().index('PARK'))
                self.assertEqual(0,m.tc['tool_number'])

    def test_failure_moves_to_safe_y_then_pause_then_park(self):
        m=Machine()
        m.printer['toolhead']['position'].y=4
        m.fail_next=True
        m.run('T1')
        retreat=next(i for i,(n,p) in enumerate(m.commands) if n=='G0' and p.get('Y')=='116.0')
        self.assertLess(retreat,m.names().index('PAUSE'))
        self.assertLess(m.names().index('PAUSE'),m.names().index('PARK'))
        self.assertTrue(m.state['failed'])
        self.assertTrue(m.printer['pause_resume']['is_paused'])
        self.assertTrue(any('previous T0, target T1' in msg for msg in m.messages))

    def test_failure_uses_per_tool_safe_y_and_restores_coordinate_mode(self):
        m=Machine()
        m.printer['tool T1']['params_safe_y']=165
        m.printer['toolhead']['position'].y=4
        m.absolute=False
        m.fail_next=True
        m.run('T1')
        self.assertTrue(any(n=='G0' and float(p.get('Y', 'nan')) == 161 for n,p in m.commands))
        self.assertFalse(m.absolute)

    def test_out_of_bounds_retreat_pauses_without_park(self):
        m=Machine()
        m.printer['tool T1']['params_safe_y']=500
        m.fail_next=True
        m.run('T1')
        self.assertTrue(m.printer['pause_resume']['is_paused'])
        self.assertNotIn('PARK',m.names())
        self.assertNotIn('G0',m.names())

    def test_wrong_manually_mounted_tool_blocks_resume(self):
        m=Machine()
        m.fail_next=True
        m.run('T1')
        m.tc['detected_tool_number']=0
        with self.assertRaisesRegex(MacroError,'manually mount'):
            m.run('RESUME')
        self.assertEqual(0,m.native_resumes)

    def test_manual_recovery_does_not_retry_docking(self):
        m=Machine()
        m.fail_next=True
        m.run('T1')
        m.tc['detected_tool_number']=1
        before=m.names().count('SELECT_TOOL')
        m.run('RESUME')
        self.assertEqual(before,m.names().count('SELECT_TOOL'))
        self.assertEqual(1,m.native_resumes)
        self.assertFalse(m.state['failed'])
        self.assertEqual(dict(x=150.,y=150.,z=10.),vars(m.printer['toolhead']['position']))

    def test_internal_failure_stops_following_startup_commands(self):
        m=Machine()
        m.fail_next=True
        m.printer['gcode_macro START_PRINT'].update(initial_tool=1,extruder_temp=235)
        with self.assertRaisesRegex(MacroError,'remaining macro operations'):
            m.run('_MODULE_EXTRUDER_HEATING')
        self.assertNotIn('M109',m.names())

    def test_failed_cancel_requires_manual_t0_then_cancels(self):
        m=Machine()
        m.fail_next=True
        m.run('T1')
        with self.assertRaisesRegex(MacroError,'Manually mount T0'):
            m.run('CANCEL_PRINT')
        m.tc['detected_tool_number']=0
        m.run('CANCEL_PRINT')
        self.assertFalse(m.state['failed'])
        self.assertEqual(0,m.tc['tool_number'])

    def test_no_cleanup_or_standby_temperature_by_default(self):
        m=Machine()
        m.run('T1')
        self.assertNotIn('PURGE',m.names())
        self.assertNotIn('CLEAN_NOZZLE',m.names())
        self.assertNotIn('SET_TOOL_TEMPERATURE',m.names())
        restores=[p for name,p in m.commands if name=='RESTORE_GCODE_STATE']
        self.assertFalse(any(p.get('NAME')=='KLIPPAIN_TOOLCHANGE_CLEANUP' for p in restores))
        self.assertFalse(any(p.get('MOVE','0')=='1' for p in restores))

    def test_purge_and_wipe_are_independently_optional(self):
        for purge,wipe in [(True,False),(False,True),(True,True)]:
            with self.subTest(purge=purge,wipe=wipe):
                m=Machine()
                m.vars.update(toolchanger_purge_on_change=purge,toolchanger_wipe_on_change=wipe)
                m.run('T1')
                self.assertEqual(purge,'PURGE' in m.names())
                self.assertEqual(wipe,'CLEAN_NOZZLE' in m.names())

    def test_active_tool_pa_shaper_sensors_leds(self):
        m=Machine()
        m.run('T1')
        pa=[p for n,p in m.commands if n=='SET_PRESSURE_ADVANCE'][-1]
        self.assertEqual('extruder1',pa['EXTRUDER'])
        self.assertEqual('0.03',pa['ADVANCE'])
        shaper=[p for n,p in m.commands if n=='SET_INPUT_SHAPER'][-1]
        self.assertEqual('41',shaper['SHAPER_FREQ_X'])
        self.assertFalse(m.printer['filament_switch_sensor T0_runout']['enabled'])
        self.assertTrue(m.printer['filament_switch_sensor T1_runout']['enabled'])
        final_effects = [p['EFFECT'] for n,p in m.commands if n=='SET_LED_EFFECT'][-4:]
        self.assertEqual({'tc_t0_logo_standby', 'tc_t0_nozzle_standby',
                          'tc_t1_logo_printing', 'tc_t1_nozzle_on'},
                         set(final_effects))

    def test_shaper_zero_is_applied_when_switching_back(self):
        m=Machine()
        m.printer['tool T0']['params_input_shaper_freq_x']=0
        m.run('T1\nT0')
        shaper=[p for n,p in m.commands if n=='SET_INPUT_SHAPER'][-1]
        self.assertEqual('0',shaper['SHAPER_FREQ_X'])

    def test_nested_filament_suppression_across_toolchange(self):
        m=Machine()
        m.run('_KLIPPAIN_DISABLE_FILAMENT_SENSORS\n_KLIPPAIN_DISABLE_FILAMENT_SENSORS\nT1')
        self.assertFalse(m.printer['filament_switch_sensor T1_runout']['enabled'])
        m.run('_KLIPPAIN_ENABLE_FILAMENT_SENSORS')
        self.assertFalse(m.printer['filament_switch_sensor T1_runout']['enabled'])
        m.run('_KLIPPAIN_ENABLE_FILAMENT_SENSORS')
        self.assertFalse(m.printer['filament_switch_sensor T0_runout']['enabled'])
        self.assertTrue(m.printer['filament_switch_sensor T1_runout']['enabled'])

    def test_single_extruder_temperature_and_pause_paths(self):
        m=Machine()
        m.vars['klippain_toolchanger_enabled']=False
        m.run('M104 S230\nM109 S240\nPAUSE\nRESUME')
        self.assertEqual(240,m.printer['extruder']['target'])
        self.assertNotIn('SELECT_TOOL',m.names())


if __name__=='__main__':
    unittest.main()
