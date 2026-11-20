#!/usr/bin/env python3
"""Run real Klippy config/temperature/profile checks with a Linux MCU dictionary.

Use disposable Klipper/klipper-toolchanger checkouts. Install the upstream host
requirements and link tool.py, toolchanger.py, tool_probe.py and
 tool_probe_endstop.py into that Klipper's klippy/extras first. No printer is
connected; Klippy's -i/-o debug mode writes MCU commands to temporary files.
This does not validate physical docking, probing, or sensor polarity.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from toolchanger_config_helpers import install_tree
from toolchanger_harness import config

ROOT = Path(__file__).resolve().parents[1]


def led_assertions(count):
    """Observe the real plugin's enabled states, including pixel replacement."""
    script = ['SET_LED_EFFECT EFFECT=smoke_unrelated', 'STATUS_LEDS COLOR=printing']

    def expect(names):
        script.append('_SMOKE_ASSERT_EFFECTS EXPECT=' + ','.join(sorted(names | {'smoke_unrelated'})))

    active = {f'tc_t{n}_{group}_{suffix}' for n in range(count)
              for group, suffix in [('logo', 'printing' if n == 1 else 'standby'),
                                    ('nozzle', 'on' if n == 1 else 'standby')]}
    expect(active)
    # REPLACE must touch the selected logo only, retaining its nozzle and all
    # other tools/independent chains. Raw writes must stop exactly that group.
    script.append('SET_LOGO_LEDS_OFF T=0')
    active.remove('tc_t0_logo_standby')
    active.add('tc_t0_logo_off')
    expect(active)
    script.append('_SET_LOGO_LEDS T=0 RED=0.25 BLUE=0.75')
    active.remove('tc_t0_logo_off')
    expect(active)
    script.append('SET_NOZZLE_LEDS_OFF')
    active.remove('tc_t1_nozzle_on')
    active.add('tc_t1_nozzle_off')
    expect(active)
    script.append('_SET_NOZZLE_LEDS T=1 GREEN=0.5')
    active.remove('tc_t1_nozzle_off')
    expect(active)
    # Full-chain startup replaces this tool's groups, then ordinary status
    # replaces startup again without disturbing a different chain.
    script.append('SET_LED_EFFECT EFFECT=tc_t0_startup REPLACE=1')
    active.discard('tc_t0_nozzle_standby')
    active.add('tc_t0_startup')
    expect(active)
    for color in ['error', 'off', 'ready']:
        script.append(f'STATUS_LEDS COLOR={color}')
        suffix = 'standby' if color == 'ready' else color
        active = {f'tc_t{n}_{group}_{suffix}' for n in range(count)
                  for group in ['logo', 'nozzle']}
        expect(active)
    # The tool flag is independent from the legacy effects flag. Disabled
    # status commands must leave existing plugin state unchanged.
    script.extend(['SET_GCODE_VARIABLE MACRO=_USER_VARIABLES VARIABLE=toolchanger_leds_enabled VALUE=False',
                   'STATUS_LEDS COLOR=printing'])
    expect(active)
    script.extend(['SET_GCODE_VARIABLE MACRO=_USER_VARIABLES VARIABLE=toolchanger_leds_enabled VALUE=True',
                   'SET_GCODE_VARIABLE MACRO=_USER_VARIABLES VARIABLE=caselight_on_at_startup VALUE=False',
                   '_INIT_LEDS'])
    expect({f'tc_t{n}_{group}_off' for n in range(count) for group in ['logo', 'nozzle']})
    script.extend(['SET_GCODE_VARIABLE MACRO=_USER_VARIABLES VARIABLE=caselight_on_at_startup VALUE=True',
                   '_INIT_LEDS'])
    expect({f'tc_t{n}_{group}_standby' for n in range(count) for group in ['logo', 'nozzle']})
    return '\n'.join(script)


def run_case(klipper, dictionary, mechanism, count, size, directory, *, shared_leds=False):
    installed = install_tree(directory / 'config', mechanism=mechanism, simulated=True)
    if shared_leds:
        # The documented customization is made on copied user templates and
        # copied real effect profiles. No replacement plugin/macro is used.
        for number in range(count):
            tool = installed / f'toolchanger/10-T{number}.cfg'
            source = tool.read_text().replace(
                f'[include ../config/hardware/lights/toolchanger/T{number}.cfg]',
                f'[include smoke-effects-T{number}.cfg]')
            source = source.replace(f"params_led_name: 'T{number}_leds'", "params_led_name: 'smoke_shared'")
            source = source.replace("params_led_logo_idx: '1'", f"params_led_logo_idx: '{number * 3 + 1}'")
            source = source.replace("params_led_nozzle_idx: '2,3'", f"params_led_nozzle_idx: '{number * 3 + 2},{number * 3 + 3}'")
            tool.write_text(source)
            effects = (ROOT / f'config/hardware/lights/toolchanger/T{number}.cfg').read_text()
            replacements = {
                f'neopixel:T{number}_leds (1)': f'neopixel:smoke_shared ({number * 3 + 1})',
                f'neopixel:T{number}_leds (2,3)': f'neopixel:smoke_shared ({number * 3 + 3}-{number * 3 + 2})',
                f'neopixel:T{number}_leds': f'neopixel:smoke_shared ({number * 3 + 1}-{number * 3 + 3})',
            }
            for old, new in replacements.items():
                effects = effects.replace(old, new)
            (installed / f'toolchanger/smoke-effects-T{number}.cfg').write_text(effects)
    main = f'''[mcu]
serial: /dev/unused
[printer]
kinematics: cartesian
max_velocity: 300
max_accel: 3000
max_z_velocity: 15
max_z_accel: 200
[stepper_x]
step_pin: gpio1
dir_pin: gpio2
endstop_pin: gpio3
microsteps: 16
rotation_distance: 40
position_endstop: 0
position_max: {size}
[stepper_y]
step_pin: gpio4
dir_pin: gpio5
endstop_pin: gpio6
microsteps: 16
rotation_distance: 40
position_endstop: 0
position_max: {size}
[stepper_z]
step_pin: gpio7
dir_pin: gpio8
microsteps: 16
rotation_distance: 8
position_min: -5
position_max: {size}
[virtual_sdcard]
path: {directory}
[pause_resume]
[respond]
[force_move]
enable_force_move: True
[neopixel smoke_indicator]
pin: gpio10
chain_count: 1
[led_effect smoke_unrelated]
leds: neopixel:smoke_indicator
layers: static 0 0 top (0.1,0.2,0.3)
[include {ROOT}/user_templates/variables.cfg]
[include {ROOT}/config/hardware/toolchanger.cfg]
[include {ROOT}/config/hardware/probes/toolchanger_tap.cfg]
[include {installed}/toolchanger/tools_{count}.cfg]
[include {ROOT}/macros/base/pause_resume.cfg]
[include {ROOT}/macros/base/park.cfg]
[include {ROOT}/macros/base/start_print.cfg]
[include {ROOT}/macros/base/toolchanger_start.cfg]
[include {ROOT}/macros/helpers/prime_line.cfg]
[include {ROOT}/macros/base/end_print.cfg]
[include {ROOT}/macros/base/cancel_print.cfg]
[include {ROOT}/macros/base/homing/homing_conditional.cfg]
[save_variables]
filename: {directory}/save_variables.cfg
'''
    if shared_leds:
        main += f'[neopixel smoke_shared]\npin: gpio11\nchain_count: {count * 3}\n'
    # Load the actual startup LED macro without scheduling unrelated startup
    # checks or replacing any product macro with a simplified implementation.
    startup = config('macros/miscs/startup.cfg').get('gcode_macro _INIT_LEDS', 'gcode')
    main += '[gcode_macro _INIT_LEDS]\ngcode:\n' + '\n'.join('    ' + line for line in startup.splitlines()) + '\n'
    main += '''[gcode_macro _SMOKE_ASSERT_EFFECTS]
gcode:
    {% set expected = params.EXPECT.split(',') %}
    {% set enabled = namespace(names=[]) %}
    {% for name in printer %}
        {% if name.startswith('led_effect ') and printer[name].enabled %}
            {% set enabled.names = enabled.names + [name[11:]] %}
        {% endif %}
    {% endfor %}
    {% if enabled.names|sort != expected|sort %}
        { action_raise_error('Smoke: actual LED effects %s != expected %s' % (enabled.names, expected)) }
    {% endif %}
'''
    main += '''[gcode_macro _SMOKE_ASSERT]
gcode:
    {% if printer.toolchanger.tool_number != 1 or printer.toolhead.extruder != 'extruder1' %}
        { action_raise_error('Smoke: active tool/extruder mismatch') }
    {% endif %}
    {% if printer.extruder1.target != 230 or printer.extruder.target != 0 %}
        { action_raise_error('Smoke: temperature routing mismatch') }
    {% endif %}
    {% if not printer['filament_switch_sensor T1_runout'].enabled or printer['filament_switch_sensor T0_runout'].enabled %}
        { action_raise_error('Smoke: active filament sensor mismatch') }
    {% endif %}
'''
    main += '''[gcode_macro _SMOKE_ASSERT_PREHEAT]
gcode:
    {% set last = params.LAST|int %}
    {% if printer['gcode_macro START_PRINT'].tool_temperatures != {0: 215.0, last: 245.0} %}
        { action_raise_error('Smoke: persisted numeric tool target map mismatch') }
    {% endif %}
    {% for n in printer.toolchanger.tool_numbers %}
        {% set heater = 'extruder' if n == 0 else 'extruder' ~ n %}
        {% set expected = 150 if n == 0 else (245 if n == last else (230 if n == 1 else 0)) %}
        {% if printer[heater].target != expected %}
            { action_raise_error('Smoke: used-only asynchronous preheat mismatch for T%d' % n) }
        {% endif %}
    {% endfor %}
    { action_respond_info('KLIPPAIN_SMOKE_OK') }
'''
    main += '''[gcode_macro _SMOKE_ASSERT_SELECTION]
gcode:
    {% set count = params.COUNT|int %}
    {% if printer.toolchanger.tool_numbers|sort != range(count)|list %}
        { action_raise_error('Smoke: selected tools do not match count profile') }
    {% endif %}
    {% for n in range(7) %}
        {% set heater = 'extruder' if n == 0 else 'extruder' ~ n %}
        {% for section in ['mcu tool' ~ n, 'tool T' ~ n, heater, 'neopixel T' ~ n ~ '_leds', 'led_effect tc_t' ~ n ~ '_logo_standby'] %}
            {% if (section in printer) != (n < count) %}
                { action_raise_error('Smoke: count selector loaded unexpected or missing ' ~ section) }
            {% endif %}
        {% endfor %}
    {% endfor %}
'''
    cfg = directory / 'printer.cfg'
    cfg.write_text(main)
    gcode = directory / 'test.gcode'
    gcode.write_text(f'''_SMOKE_ASSERT_SELECTION COUNT={count}
_INIT_CHECK_TOOLCHANGER
INITIALIZE_TOOLCHANGER T=0
M104 T1 S220
INITIALIZE_TOOLCHANGER T=1
M109 T1 S230
{led_assertions(count)}
_SMOKE_ASSERT
INITIALIZE_TOOLCHANGER T=0
SET_GCODE_VARIABLE MACRO=_USER_VARIABLES VARIABLE=toolchanger_preheat_used_tools VALUE=True
SET_GCODE_VARIABLE MACRO=START_PRINT VARIABLE=tool_temperatures VALUE="{{0: 215.0, {count - 1}: 245.0}}"
_MODULE_EXTRUDER_PREHEATING
_SMOKE_ASSERT_PREHEAT LAST={count - 1}
''')
    args = [sys.executable, str(klipper / 'klippy/klippy.py'), str(cfg), '-i', str(gcode),
            '-o', str(directory / 'mcu-output'), '-d', str(dictionary)]
    for n in range(count):
        args += ['-d', f'tool{n}={dictionary}']
    result = subprocess.run(args, cwd=klipper, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=60)
    log = directory / 'klippy.log'
    log.write_text(result.stdout)
    errors = [line for line in result.stdout.splitlines()
              if any(token in line for token in ['ERROR:', 'Traceback', '!!',
                      'Unknown command:', 'Error evaluating', 'Error loading template',
                      'Internal error on command', 'Unhandled exception'])]
    if result.returncode or errors or 'KLIPPAIN_SMOKE_OK' not in result.stdout:
        print(result.stdout[-6000:])
        raise RuntimeError(f'Klippy smoke failed: {log}')
    print(f'PASS {mechanism}: {count} tools, {size}mm' + (' (shared LED chain)' if shared_leds else ''))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--klipper', type=Path, required=True)
    parser.add_argument('--dictionary', type=Path, required=True)
    parser.add_argument('--tools', type=int, nargs='+', default=[2,3,4,5,6,7])
    parser.add_argument('--sizes', type=int, nargs='+', default=[300,350])
    args = parser.parse_args()
    if not (args.klipper / 'klippy/extras/led_effect.py').exists():
        parser.error('The real klipper-led_effect plugin must be installed in the disposable Klipper checkout.')
    directory = Path(tempfile.mkdtemp(prefix='klippain-klippy-smoke-'))
    print(f'Results: {directory}')
    for mechanism in ['stealthchanger','tapchanger']:
        for size in args.sizes:
            for count in args.tools:
                case = directory / f'{mechanism}-{count}-{size}'
                case.mkdir()
                run_case(args.klipper.resolve(), args.dictionary.resolve(), mechanism, count, size, case)
    for count, size in [(min(args.tools), min(args.sizes)), (max(args.tools), max(args.sizes))]:
        case = directory / f'shared-{count}-{size}'
        if case.exists():
            continue
        case.mkdir()
        run_case(args.klipper.resolve(), args.dictionary.resolve(), 'stealthchanger', count, size,
                 case, shared_leds=True)


if __name__ == '__main__':
    main()
