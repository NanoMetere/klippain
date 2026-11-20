"""Execute repository Jinja templates with Klipper's render-then-execute semantics.

No firmware, heaters or motors are touched. Hardware primitives are recorded and
modelled; unknown integration commands fail instead of silently passing.
"""
from __future__ import annotations

import ast
import configparser
import copy
import re
import shlex
from pathlib import Path
from types import SimpleNamespace

import jinja2

ROOT = Path(__file__).resolve().parents[1]


def config(path):
    parser = configparser.RawConfigParser(
        strict=False, interpolation=None, inline_comment_prefixes=("#", ";"))
    parser.read(ROOT / path)
    return parser


class MacroError(Exception):
    pass


class Printer(dict):
    def __getitem__(self, key):
        return super().__getitem__(str(key))


class Machine:
    def __init__(self, count=2, size=300):
        self.env = jinja2.Environment('{%', '%}', '{', '}')
        self.macros = {}
        self.printer = Printer()
        self.commands = []
        self.moves = []
        self.events = []
        self.renders = []
        self.command_hooks = {}
        self.messages = []
        self.saved = {}
        self.fail_next = False
        self.inside_homing = False
        self.absolute = True
        self.native_pauses = 0
        self.native_resumes = 0
        paths = [
            'user_templates/variables.cfg', 'config/hardware/probes/toolchanger_tap.cfg',
            'config/hardware/lights/config_colors.cfg',
            'config/hardware/toolchanger.cfg',
            'macros/hardware_functions/toolchanger.cfg',
            'macros/hardware_functions/filament_sensors.cfg',
            'macros/hardware_functions/status_leds.cfg',
            'macros/base/start_print.cfg', 'macros/base/end_print.cfg',
            'macros/base/toolchanger_start.cfg',
            'macros/base/cancel_print.cfg', 'macros/base/pause_resume.cfg',
            'macros/base/park.cfg', 'macros/base/homing/homing_override.cfg',
            'macros/base/homing/homing_conditional.cfg',
            'macros/base/homing/tilting.cfg',
            'macros/base/probing/generic_probe.cfg',
            'macros/base/probing/virtual_z_probe.cfg',
            'macros/base/probing/overrides/toolchanger.cfg',
            'macros/base/probing/overrides/qgl.cfg',
            'macros/base/probing/overrides/z_tilt.cfg',
            'macros/base/probing/overrides/bed_mesh_calibrate.cfg',
            'macros/helpers/hotend_heater_ctrl.cfg', 'macros/helpers/temp_check.cfg',
            'macros/helpers/prime_line.cfg',
            'macros/helpers/heatsoak.cfg',
            'macros/miscs/startup.cfg',
        ]
        for path in paths:
            c = config(path)
            for section in c.sections():
                if section.startswith('gcode_macro '):
                    name = section.split(' ', 1)[1]
                    try:
                        self.macros[name.upper()] = self.env.from_string(c.get(section, 'gcode', fallback=''))
                    except jinja2.TemplateSyntaxError as error:
                        raise RuntimeError(f'{path}: {section}: {error}') from error
                    variables = self.printer.setdefault(section, {})
                    for key, value in c.items(section):
                        if key.startswith('variable_'):
                            variables[key[9:]] = ast.literal_eval(value)
                elif section == 'homing_override':
                    self.macros['_TEST_HOME'] = self.env.from_string(c.get(section, 'gcode'))
        vars = self.printer['gcode_macro _USER_VARIABLES']
        vars.update(klippain_startup_succeeded=True, klippain_mmu_enabled=False,
                    klippain_ercf_enabled=False, sensorless_homing_enabled=False,
                    x_driver='tmc2209', y_driver='tmc2209', z_driver='tmc2209',
                    bed_mesh_enabled=False, qgl_enabled=False, ztilt_enabled=False,
                    homing_first='Y', purge_and_brush_enabled=False,
                    purgeclean_servo_enabled=False, light_enabled=False,
                    status_leds_effects_enabled=False, status_leds_caselight_enabled=False,
                    status_leds_minidisplay_enabled=False, firmware_retraction_enabled=False)
        tc = dict(status='ready', tool='tool T0', tool_number=0,
                  detected_tool='tool T0', detected_tool_number=0,
                  tool_numbers=list(range(count)), tool_names=[f'tool T{i}' for i in range(count)])
        self.printer['toolchanger'] = tc
        self.printer['toolhead'] = dict(extruder='extruder', homed_axes='xyz',
            position=SimpleNamespace(x=150., y=150., z=10.),
            axis_minimum=SimpleNamespace(x=0., y=0., z=0.),
            axis_maximum=SimpleNamespace(x=float(size), y=float(size), z=float(size)),
            max_accel=3000)
        self.printer['pause_resume'] = dict(is_paused=False)
        self.printer['print_stats'] = dict(state='printing')
        self.printer['gcode_move'] = dict(
            gcode_position=SimpleNamespace(x=150.,y=150.,z=10.,e=0.),
            position=SimpleNamespace(x=150.,y=150.,z=10.,e=0.),
            homing_origin=SimpleNamespace(x=0.,y=0.,z=0.),
            absolute_coordinates=True, absolute_extrude=False)
        self.printer['heater_bed'] = dict(target=60., temperature=60.)
        self.printer['gcode'] = dict(commands=list(self.macros) + ['SET_LED_EFFECT', 'STOP_LED_EFFECTS'])
        settings = dict(printer=dict(kinematics='corexy', max_velocity=300,
                max_accel=3000, square_corner_velocity=5),
            stepper_x=dict(position_endstop=300), stepper_y=dict(position_endstop=300),
            stepper_z=dict(endstop_pin='probe:z_virtual_endstop', homing_positive_dir=False),
            input_shaper={}, idle_timeout=dict(timeout=1800),
            pause_resume=dict(recover_velocity=50.))
        self.printer['configfile'] = dict(settings=settings, config=settings)
        self.printer['probe'] = dict(last_query=False)
        for n in range(count):
            extruder = 'extruder' if n == 0 else f'extruder{n}'
            self.printer[f'tool T{n}'] = dict(name=f'tool T{n}', tool_number=n,
                extruder=extruder, heater=extruder, detect_state='mounted',
                params_safe_y=120., params_docks_calibrated=True,
                params_pressure_advance=0.02 + n * .01,
                params_input_shaper_freq_x=40 + n, params_input_shaper_freq_y=50 + n,
                params_filament_sensors=[f'T{n}_runout'], params_led_name=f'T{n}_leds',
                params_led_effect_prefix=f'tc_t{n}', params_led_logo_idx='1',
                params_led_nozzle_idx='2,3', gcode_x_offset=0., gcode_y_offset=0.,
                gcode_z_offset=0.)
            self.printer[extruder] = dict(target=220.+n, temperature=220.+n, can_extrude=True,
                                          pressure_advance=.02+n*.01)
            self.printer[f'filament_switch_sensor T{n}_runout'] = dict(enabled=n == 0)
            self.printer[f'neopixel T{n}_leds'] = dict(color_data=[(0., 0., 0., 0.)] * 3)
            settings[f'neopixel T{n}_leds'] = dict(chain_count=3)
            settings[f'tool T{n}'] = dict(tool_probe=f'tool_probe T{n}')
            settings[extruder] = dict(min_extrude_temp=170, filament_diameter=1.75,
                                     max_extrude_cross_section=5, nozzle_diameter=.4,
                                     min_temp=0, max_temp=300)
            profile = config(f'config/hardware/lights/toolchanger/T{n}.cfg')
            for section in profile.sections():
                self.printer[section] = dict(enabled=False)
                settings[section] = dict(profile.items(section))
        self.set_active(0)

    @property
    def vars(self):
        return self.printer['gcode_macro _USER_VARIABLES']

    @property
    def tc(self):
        return self.printer['toolchanger']

    @property
    def state(self):
        return self.printer['gcode_macro _KLIPPAIN_TOOLCHANGER']

    def set_active(self, n):
        previous = self.printer.get(self.tc.get('tool', ''), {})
        selected = self.printer[f'tool T{n}']
        # SELECT_TOOL RESTORE_AXIS=XYZ restores nozzle coordinates through the
        # pinned backend's ToolGcodeTransform. The toolhead status is physical;
        # gcode_move.position is before that transform.
        for axis in 'xyz':
            pos = self.printer['toolhead']['position']
            setattr(pos, axis, getattr(pos, axis)
                    - previous.get(f'gcode_{axis}_offset', 0.)
                    + selected.get(f'gcode_{axis}_offset', 0.))
        self.tc.update(tool=f'tool T{n}', tool_number=n,
                       detected_tool=f'tool T{n}', detected_tool_number=n)
        self.printer['toolhead']['extruder'] = self.printer[f'tool T{n}']['extruder']

    def finish_temperature_wait(self, heater_name, temperature):
        """Model a successful wait only for that heater and its tool extruders."""
        self.printer[heater_name]['temperature'] = temperature
        for name in self.tc['tool_names']:
            tool = self.printer[name]
            if tool.get('heater', tool['extruder']) == heater_name:
                extruder = tool['extruder']
                minimum = self.printer['configfile']['settings'][extruder]['min_extrude_temp']
                self.printer[extruder]['can_extrude'] = temperature >= minimum

    def render(self, name, params=None):
        params = params or {}
        self.renders.append(dict(name=name, params=params.copy(), tool=self.tc['tool_number'],
                                 extruder=self.printer['toolhead']['extruder'],
                                 start=copy.deepcopy(self.printer.get('gcode_macro START_PRINT', {}))))
        def raise_error(message):
            raise MacroError(message)
        context = copy.deepcopy(self.printer.get('gcode_macro ' + name, {}))
        context.update(printer=copy.deepcopy(self.printer), params=params,
                       rawparams=' '.join(f'{k}={v}' for k,v in params.items()),
                       action_raise_error=raise_error,
                       action_respond_info=lambda message: self.messages.append(message) or '')
        return self.macros[name].render(context)

    def run(self, script):
        for line in script.splitlines():
            line = line.split('#', 1)[0].split(';', 1)[0].strip()
            if not line:
                continue
            pieces = shlex.split(line)
            name = pieces[0].upper()
            params = {}
            for item in pieces[1:]:
                if '=' in item:
                    key,value = item.split('=',1)
                    params[key.upper()] = value
                else:
                    match = re.fullmatch(r'([A-Za-z]+)(.*)', item)
                    if match:
                        params[match[1].upper()] = match[2]
            self.commands.append((name, params))
            self.events.append(dict(name=name, params=params.copy(), tool=self.tc['tool_number'],
                                    targets={n: self.printer[self.printer[tool]['heater']]['target']
                                             for n, tool in zip(self.tc['tool_numbers'], self.tc['tool_names'])
                                             if tool in self.printer}))
            if name == 'G28' and not self.inside_homing:
                self.inside_homing = True
                try:
                    self.run(self.render('_TEST_HOME', params))
                finally:
                    self.inside_homing = False
            elif name in self.macros:
                self.run(self.render(name, params))
            elif name == 'SET_GCODE_VARIABLE':
                state = self.printer['gcode_macro ' + params['MACRO']]
                if params['VARIABLE'] not in state:
                    raise MacroError('undeclared variable ' + params['VARIABLE'])
                state[params['VARIABLE']] = ast.literal_eval(params['VALUE'])
            elif name == 'INITIALIZE_TOOLCHANGER':
                self.set_active(int(params.get('T',self.tc['detected_tool_number'])))
                self.tc['status'] = 'initializing'
                self.run('_KLIPPAIN_TOOLCHANGE_AFTER')
                self.tc['status'] = 'ready'
            elif name == 'SELECT_TOOL':
                n = int(params['T'])
                if self.tc['tool_number'] == n:
                    continue
                previous = self.tc['tool_number']
                position = copy.deepcopy(self.printer['toolhead']['position'])
                self.run('SAVE_GCODE_STATE NAME=_toolchange_state')
                self.tc['status'] = 'changing'
                self.run(f'_KLIPPAIN_TOOLCHANGE_BEFORE FROM={previous} TO={n}')
                self.set_active(n)
                if self.fail_next:
                    self.fail_next = False
                    self.tc['status'] = 'error'
                    self.tc['detected_tool_number'] = -1
                    self.run('RESTORE_GCODE_STATE NAME=_toolchange_state MOVE=0')
                    self.run(f'_KLIPPAIN_TOOLCHANGE_ERROR TARGET={n} CHANGING=1')
                    # Upstream repairs native PAUSE_STATE after the error hook.
                    absolute, _, gmove = self.saved['PAUSE_STATE']
                    self.saved['PAUSE_STATE'] = (absolute, position, gmove)
                else:
                    self.run('_KLIPPAIN_TOOLCHANGE_AFTER')
                    self.run('RESTORE_GCODE_STATE NAME=_toolchange_state MOVE=0')
                    self.tc['status'] = 'ready'
            elif name == 'SET_FILAMENT_SENSOR':
                for prefix in ['filament_switch_sensor ', 'filament_motion_sensor ']:
                    if prefix + params['SENSOR'] in self.printer:
                        self.printer[prefix + params['SENSOR']]['enabled'] = bool(int(params['ENABLE']))
            elif name == 'BASE_PAUSE':
                self.native_pauses += 1
                self.printer['pause_resume']['is_paused'] = True
                self.saved['PAUSE_STATE'] = (self.absolute,
                    copy.deepcopy(self.printer['toolhead']['position']),
                    copy.deepcopy(self.printer['gcode_move']))
            elif name == 'BASE_RESUME':
                if 'PAUSE_STATE' not in self.saved:
                    raise MacroError('missing native pause state')
                self.native_resumes += 1
                self.printer['pause_resume']['is_paused'] = False
                absolute, position, gmove = self.saved['PAUSE_STATE']
                self.absolute = absolute
                self.printer['toolhead']['position'] = copy.deepcopy(position)
                self.printer['gcode_move'] = copy.deepcopy(gmove)
            elif name == 'SAVE_GCODE_STATE':
                self.saved[params['NAME']] = (self.absolute, copy.deepcopy(self.printer['toolhead']['position']),
                                             copy.deepcopy(self.printer['gcode_move']))
            elif name == 'RESTORE_GCODE_STATE':
                absolute, position, gmove = self.saved[params['NAME']]
                self.absolute = absolute
                self.printer['gcode_move']['absolute_coordinates'] = absolute
                if params.get('MOVE','0') == '1':
                    self.printer['toolhead']['position'] = copy.deepcopy(position)
                    self.printer['gcode_move'] = gmove
            elif name == 'G90':
                self.absolute = True
                self.printer['gcode_move']['absolute_coordinates'] = True
            elif name == 'G91':
                self.absolute = False
                self.printer['gcode_move']['absolute_coordinates'] = False
            elif name in ['M82', 'M83']:
                self.printer['gcode_move']['absolute_extrude'] = name == 'M82'
            elif name in ['G0','G1']:
                pos = self.printer['toolhead']['position']
                start = tuple(getattr(pos, key) for key in ['x', 'y', 'z'])
                gmove = self.printer['gcode_move']
                tool = self.printer[self.tc['tool']]
                gcode_start = tuple(getattr(gmove['gcode_position'], key) for key in 'xyz')
                for key in ['X','Y','Z']:
                    if key in params:
                        axis = key.lower()
                        value = float(params[key])
                        offset = tool.get(f'gcode_{axis}_offset', 0.)
                        base = getattr(gmove['position'], axis) - getattr(gmove['gcode_position'], axis)
                        physical = value + base + offset if self.absolute else getattr(pos, axis) + value
                        setattr(pos, axis, physical)
                        setattr(gmove['position'], axis, physical - offset)
                        setattr(gmove['gcode_position'], axis, physical - offset - base)
                self.moves.append(dict(tool=self.tc['tool_number'], command=name,
                                       params=params.copy(), start=start,
                                       end=tuple(getattr(pos, key) for key in ['x', 'y', 'z']),
                                       gcode_start=gcode_start,
                                       gcode_end=tuple(getattr(gmove['gcode_position'], key) for key in 'xyz')))
            elif name == 'G92':
                for key in 'XYZE':
                    if key in params:
                        setattr(self.printer['gcode_move']['gcode_position'], key.lower(), float(params[key]))
            elif name == 'SET_GCODE_OFFSET':
                gmove = self.printer['gcode_move']
                for axis in 'xyz':
                    key = axis.upper()
                    if key in params or key + '_ADJUST' in params:
                        old = getattr(gmove['homing_origin'], axis)
                        new = float(params[key]) if key in params else old + float(params[key + '_ADJUST'])
                        setattr(gmove['homing_origin'], axis, new)
                        setattr(gmove['gcode_position'], axis,
                                getattr(gmove['gcode_position'], axis) - new + old)
            elif name == 'G28':
                if 'Z' in params or not params:
                    self.run('_KLIPPAIN_ASSERT_PROBE_TOOL')
                homed = set(self.printer['toolhead']['homed_axes'])
                homed.update(k.lower() for k in params if k in 'XYZ')
                if not params:
                    homed.update('xyz')
                self.printer['toolhead']['homed_axes'] = ''.join(sorted(homed))
            elif name == 'SET_KINEMATIC_POSITION':
                self.printer['toolhead']['homed_axes'] = 'xyz'
            elif name == 'SET_TOOL_TEMPERATURE':
                n = int(params.get('T',self.tc['tool_number']))
                heater_name = self.printer[f'tool T{n}']['heater']
                heater = self.printer[heater_name]
                heater['target'] = float(params['TARGET'])
                if int(params.get('WAIT', 0)) and heater['target'] > 0:
                    self.finish_temperature_wait(heater_name, heater['target'])
            elif name == 'SET_HEATER_TEMPERATURE':
                self.printer[params['HEATER']]['target'] = float(params['TARGET'])
            elif name == 'M104.1' or name == 'M109.1':
                heater_name = self.printer['toolhead']['extruder']
                target = float(params.get('S', 0))
                self.printer[heater_name]['target'] = target
                if name == 'M109.1' and target > 0:
                    self.finish_temperature_wait(heater_name, target)
            elif name == 'TEMPERATURE_WAIT':
                heater_name = params['SENSOR']
                temperature = self.printer[heater_name]['temperature']
                temperature = max(temperature, float(params.get('MINIMUM', temperature)))
                temperature = min(temperature, float(params.get('MAXIMUM', temperature)))
                self.finish_temperature_wait(heater_name, temperature)
            elif name == 'VERIFY_TOOL_DETECTED':
                if self.tc['detected_tool_number'] != int(params.get('T',self.tc['tool_number'])):
                    raise MacroError('tool detection mismatch')
            elif name == 'CLEAR_PAUSE':
                self.printer['pause_resume']['is_paused'] = False
            elif name in ['_BASE_QUAD_GANTRY_LEVEL','_BASE_Z_TILT_ADJUST','_BASE_BED_MESH_CALIBRATE',
                          '_KLIPPAIN_BASE_PROBE','_KLIPPAIN_BASE_PROBE_ACCURACY','_KLIPPAIN_BASE_PROBE_CALIBRATE']:
                self.run('_KLIPPAIN_ASSERT_PROBE_TOOL')
            elif name in {
                    'G4', 'G10', 'G11', 'M84', 'M106', 'M107', 'M117',
                    'M220', 'M221', 'M400', 'RESPOND',
                    'SET_INPUT_SHAPER', 'SET_PRESSURE_ADVANCE', 'SET_RETRACTION',
                    'SET_IDLE_TIMEOUT', 'SET_VELOCITY_LIMIT', 'SET_TMC_CURRENT',
                    'SET_LED', 'SET_LED_EFFECT', 'STOP_LED_EFFECTS',
                    'UPDATE_DELAYED_GCODE', 'BED_MESH_CLEAR',
                    'BED_MESH_PROFILE', 'SET_PRINT_STATS_INFO_BASE',
                    'BASE_CANCEL_PRINT', 'TURN_OFF_HEATERS', 'SDCARD_RESET_FILE',
                    # Optional cleanup hardware is outside this harness. Tests
                    # assert orchestration calls, not physical brush/purge motion.
                    'PURGE', 'CLEAN_NOZZLE', '_CONDITIONAL_MOVE_TO_PURGE_BUCKET',
                    'ADJUST_Z_AFTER_TOOL_NOZZLE_HOME'}:
                pass
            else:
                raise MacroError('unimplemented command ' + name)
            if name in self.command_hooks:
                self.command_hooks[name](self, params)
            # LED primitives are asserted separately against the real plugin.

    def names(self):
        return [name for name, _ in self.commands]
