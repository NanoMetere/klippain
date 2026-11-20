# Toolchanger support

Klippain integrates with [viesturz/klipper-toolchanger](https://github.com/viesturz/klipper-toolchanger).
Enable it explicitly; ordinary single-extruder and Happy Hare configurations keep their existing workflow.
The initial mechanical presets support StealthChanger and TapChanger with fixed docks, 2–7 independent
extruders, and tool numbers T0–T6. The supplied count selectors configure consecutive tools from T0;
custom sparse tool sets can use explicit tool includes without a count selector. T0 must exist.
The motion layer uses configured coordinates, not a fixed printer size: Voron 2.4 300/350 are examples,
not a restriction. Choose QGL or Z_TILT and mesh dimensions through the usual Klippain includes.

## Installation and configuration

1. Use the reference versions below and install the upstream Toolchanger extension using its instructions. Do **not**
   include its `macros.cfg`, example homing override, PAUSE or RESUME macros: Klippain supplies these.
   Install [klipper-led_effect](https://github.com/julianschill/klipper-led_effect) for the per-tool LED
   effects included by the supplied tool templates.
2. Copy the **contents** of `user_templates/toolchanger/` into
   `~/printer_data/config/toolchanger/` once, then edit those copies. These optional templates are
   excluded from the installer's normal template flattening. When migrating an existing setup, add
   the new selector and any missing per-tool files without replacing your edited `00-toolchanger.cfg`
   or any customized `10-T*.cfg`. Include the integration and per-tool TAP profile first, then uncomment exactly one
   tool count selector. The selector loads `00-toolchanger.cfg` and only the required numbered tools:

   ```ini
   [include config/hardware/toolchanger.cfg]
   [include config/hardware/probes/toolchanger_tap.cfg]
   # [include toolchanger/tools_2.cfg]
   # [include toolchanger/tools_3.cfg]
   # [include toolchanger/tools_4.cfg]
   # [include toolchanger/tools_5.cfg]
   # [include toolchanger/tools_6.cfg]
   # [include toolchanger/tools_7.cfg]
   ```

   Select exactly one selector. Startup validation reports multiple selected counts or a tool list
   that does not match the selected count. Remove the old `[include toolchanger/*.cfg]` when migrating.
   For a custom sparse tool set, omit all `tools_N.cfg` selectors and explicitly include
   `toolchanger/00-toolchanger.cfg` plus only the configured `toolchanger/10-Tn.cfg` files.
   Klipper requires contiguous **extruder section names**, even when tool numbers have gaps.
   For T0/T2/T6, use `extruder`/`extruder1`/`extruder2` respectively; update the copied extruder
   and driver section names, each tool's `extruder:` reference, heater-fan `heater:` reference,
   and any motion sensor's `extruder:` reference consistently. MCU, probe, sensor and LED names
   can retain their physical tool numbers. Select exactly one mechanical preset in your
   `toolchanger/00-toolchanger.cfg`. Keep your existing
   machine, axis, QGL/Z_TILT and bed mesh includes. Remove single-tool extruder/driver/fan/LED profiles
   that would duplicate the tool files. Keep shared case/display lighting profiles if used.
3. The `10-T0.cfg`–`10-T6.cfg` files are EBB36/42 v1.2 wiring **examples**. Configure the actual MCU
   serial or CAN identity, motor, heater, thermistor, TAP polarity and LEDs for every selected tool.
   Use `extruder`, `extruder1`, … for separate hotends. Unselected tool templates are not included.
4. Measure each tool's `params_park_x/y/z`, `params_safe_y`, offsets and motion path. The zero dock
   coordinates are placeholders. Set `params_docks_calibrated: True` only after this work; selection
   is refused while it is False. The preset paths are upstream mechanical examples, not calibrated
   paths for a particular printer. Check their clearances at reduced speed.
5. Enable `[include moonraker/toolchanger.conf]` in your own `moonraker.conf` for upstream updates.
   As upstream documents, updates adding new extension files may require rerunning its installer.

New mechanical systems can supply different `params_pickup_path` / `params_dropoff_path`, or their
own pickup/dropoff G-code, while retaining the lifecycle hooks in `fixed_dock.cfg`. Per-tool hook
replacements must call the `_KLIPPAIN_TOOLCHANGE_BEFORE` / `_AFTER` helpers. Do not remove the
`error_gcode` hook. Use Tn or `_KLIPPAIN_SELECT_TOOL` for normal selections; raw SELECT_TOOL bypasses
Klippain's post-selection cleanup and enclosing-macro failure check.

### Reference versions and probe setup

This implementation uses the user's known-working reference versions:

- Klipper `v0.13.0-573-g644cda5e` (`644cda5ecaa39d0dcf797624c19d5425cb8121ec`).
- klipper-toolchanger `v0.0.0-219` (upstream has no version tags; the 219-commit revision
  inspected is `94756dfde9b729fd69f9b8780067821c5c99a528`).

Each tool uses the upstream probe:

```ini
[tool T1]
tool_number: 1
extruder: extruder1
tool_probe: tool_probe T1
# ...

[tool_probe T1]
pin: !tool1:TAP_INPUT
z_offset: 0
activate_gcode:
    _KLIPPAIN_ASSERT_PROBE_TOOL
```

Use this automatic routing on **all** tools; do not also define a separate `[probe]`,
`[tool_probe_endstop]`, legacy `tool: N` probe assignment or SET_ACTIVE_TOOL_PROBE hook.
The backend registers the standard probe object and changes its active probe on tool selection.
Keep these reference versions while validating the integration.

A single conventional `[probe]` on T0 can instead use the normal `voron_tap.cfg` profile and omit all
per-tool probe references. That arrangement requires manually mounting T0 for cold Z homing and
cannot enable `toolchanger_home_with_any_tool`.

The supplied per-tool TAP activation assertion is mandatory: it protects probe sessions started
internally by Klipper as well as the public macro wrappers. Before motion, verify tool detection
polarity: current upstream considers a **triggered detection pin absent**. This changed in February
2026. Do not copy older detection polarity or the older `tool: N` / SET_ACTIVE_TOOL_PROBE setup without
migrating it. All tools need working detection for manual recovery.

### Multiple identical toolhead boards and pin aliases

Klipper resolves aliases **per MCU**, not globally. Use distinct `[board_pins]` section names and
assign each to its MCU. For example:

```ini
[board_pins ebb_t0]
mcu: tool0
aliases: E_STEP=PD0, TAP_INPUT=PB6

[board_pins ebb_t1]
mcu: tool1
aliases: E_STEP=PD0, TAP_INPUT=PB6
```

Then `tool0:E_STEP` and `tool1:E_STEP` refer to different physical pins. This avoids duplicating
Klippain's existing single-`toolhead` manufacturer includes, whose MCU assignment is fixed. The
per-tool templates show complete scoped aliases; adapt them for other board types. Upstream tool_probe permits the shared TAP/detection input on each tool.

## Startup and probing

Every QGL, Z_TILT, mesh, PROBE, PROBE_ACCURACY and PROBE_CALIBRATE operation selects T0 before
probing. Native probe sessions also assert that the detected, selected tool is T0. Probing temperatures
are checked on the **active** hotend. Tool offsets remain the backend's responsibility; the reference
Toolchanger's independent transform coexists with Klippain's user Z adjustment.

Default cold-start behavior requires T0 mounted. If another tool is mounted and XYZ position is
unknown, Klippain refuses to drive to a fixed dock with an unknown Z position. Once XYZ is homed,
Z rehoming selects T0 automatically.

When every tool has its own TAP, this optional variable allows startup with another tool mounted:

```ini
[gcode_macro _USER_VARIABLES]
variable_toolchanger_home_with_any_tool: True
```

The sequence is: initialize/detect the mounted tool → home using that tool's TAP → account for its
nozzle offset → select T0 → home Z again using T0. This initial home is the only exception to T0-only
probing. QGL/Z_TILT, mesh and later probe commands still use T0. No tool detected, missing per-tool
probe configuration, or ambiguous detection stops the operation.

START_PRINT finishes all T0 probing before `_MODULE_EXTRUDER_HEATING` selects `INITIAL_TOOL`. When
the slicer supplies used-tool `Tn_TEMP` parameters, Klippain retains that full used-tool map for
heating and temperature checks. `variable_toolchanger_preheat_used_tools` defaults to `False`:
startup does not bulk-heat parked used tools in either early preheating or final heating. The initial
tool is still heated and waited for normally, and each other tool is heated and waited for at its
primeline. If a tool was not primed or preheated, the slicer must set its target and issue a
selected-tool `M109` wait before its first extrusion. Set preheating to
`True` to asynchronously start declared used non-T0 targets during early preheating; after probing,
the final heating action starts all declared used-tool targets, including T0 if listed. A used T0
stays at its safe probing temperature until probing completes. An unused T0 may still receive the
configured safe temperature needed by its probe. No `Tn_TEMP` list retains the legacy initial-tool-only
`EXTRUDER_TEMP` behavior.

The optional `primeline` action defaults to one line for every tool declared by `Tn_TEMP`, with the
initial tool last. Set `variable_toolchanger_prime_all_used_tools: False` to prime only the initial
tool; this does not remove other declared tools from the full temperature map. Each line waits for
its tool's target before extrusion, including when bulk preheating is off. The planner builds bounds,
widths, lanes, ordering and feasibility only for the selected prime set, then preflights its lines
before its first tool change or extrusion. Keep `extruder_heating` after every probing action and
`primeline` after `extruder_heating`; startup validation rejects incompatible ordering. Custom start
actions must follow the same ordering. Global material settings (filter, firmware retraction, user Z
adjustment) come from the initial material; tool-specific PA comes from each tool's profile or
extruder settings.

If startup fails after some tools have primed, a full START_PRINT retry resets its per-attempt
progress and prints those declared lines again. Clear the previous material from the priming strip
before resuming or retrying the startup.

Per-tool primelines use parallel lanes perpendicular to the configured `prime_line_direction`, with
at least 2 mm between the widest adjacent line edges. The planner honors each extruder's filament
diameter and maximum extrusion cross-section, tool XY offsets, user G-code offsets, axis travel
limits, the adaptive preferred origin, and the configured bed bounds. By default the printable
rectangle is `[max(axis_minimum, 0), axis_maximum]` on each axis. If machine travel includes docks or
other off-bed space, narrow the printable rectangle with these optional variables in
`variables.cfg`:

```ini
[gcode_macro _USER_VARIABLES]
variable_toolchanger_prime_bed_min: [0, 0]
variable_toolchanger_prime_bed_max: [300, 300]
```

Set them to the actual printable bed coordinates for your machine. The planner rejects a layout if
all lines in the selected prime set cannot fit. Adaptive placement uses the slicer's first-layer bounds (or
`exclude_object`) as its preferred origin; it does not detect arbitrary model occupancy. Calibrate a
clear priming strip that remains free of first-layer objects.

## Tool-specific behavior

Put these optional values in each `[tool ...]` section. They use upstream's ordinary `params_*`
extension mechanism:

| Parameters | Behavior |
| --- | --- |
| `params_pressure_advance`, `params_pressure_advance_smooth_time` | Applied to the selected extruder. If PA is omitted, its own extruder setting is retained; START_PRINT does not overwrite it with the initial material's PA. |
| `params_input_shaper_freq_x/y`, `params_input_shaper_type_x/y`, `params_input_shaper_damping_ratio_x/y` | Both axes are explicitly set on activation. Missing values fall back to `[input_shaper]` or zero/defaults, never the previous tool's shaper. |
| `params_filament_sensors: ['T0_runout', 'T0_motion']` | Only the active tool's sensors are enabled. Works with Klippain's nested temporary sensor suppression during loading/purging. Each sensor still defines its usual pause/runout policy. |
| `params_led_name`, `params_led_effect_prefix`, `params_led_logo_idx`, `params_led_nozzle_idx` | A distinct neopixel chain, effect-name prefix and index groups. STATUS_LEDS shows the active tool's state, parked tools in standby, and all tools in error/off when appropriate. Shared case/display LEDs retain their existing behavior. |

#### Per-tool LED effects

The supplied `10-T0.cfg`–`10-T6.cfg` templates include the matching
`config/hardware/lights/toolchanger/TN.cfg` profile. Each profile scopes Klippain's existing
`status_leds_effects.cfg` patterns to that tool's logo, nozzle and whole-chain startup/error effects.
Only effect profiles included by selected tool files are loaded. Keep the matching
`params_led_effect_prefix: 'tc_tN'`; startup checks that every configured tool's chain, unique prefix
and required effects exist. It also checks effect selectors against each tool's chain and pixel
ownership, including overlap when multiple tools share a chain. Tool status effects use `klipper-led_effect` even when the global
`status_leds_effects_enabled` variable is False, so shared caselight/display behavior remains under
its existing controls.

When migrating edited per-tool files, add the matching effect prefix and include without replacing
your hardware values:

```ini
[tool T0]
params_led_effect_prefix: 'tc_t0'

[include ../config/hardware/lights/toolchanger/T0.cfg]
```

Repeat with the corresponding tool number and prefix. To remove LEDs from a tool, remove its effect
include and LED chain section, then clear `params_led_name`. Setting only
`variable_toolchanger_leds_enabled: False` disables Klippain's status effects, but Klipper still
loads effect sections from any included profile and therefore still requires the plugin.

`STATUS_LEDS COLOR=READY|BUSY|HEATING|LEVELING|HOMING|CLEANING|MESHING|CALIBRATING_Z|PRINTING|DONE_PRINTING|ERROR|ON|OFF|SHUTDOWN`
updates all configured tool chains. Parked tools remain in standby during ordinary states; errors,
OFF and shutdown broadcast to every tool. The last requested status is restored after tool changes.
The existing startup preference `caselight_on_at_startup` chooses READY or OFF at startup. Startup
effects remain manual, for example `SET_LED_EFFECT EFFECT=tc_t0_startup REPLACE=1 FADETIME=0.5`.

The existing public group controls target the selected tool by default, or an explicit tool without
physically changing tools:

```gcode
SET_LOGO_LEDS_OFF
SET_LOGO_LEDS_OFF T=1
SET_NOZZLE_LEDS_ON T=1
SET_NOZZLE_LEDS_OFF
```

For custom static RGB values, `_SET_LOGO_LEDS` and `_SET_NOZZLE_LEDS` accept RED/GREEN/BLUE/WHITE
and optional `T=n`; these stop only the selected tool's affected LED indices before setting them.
For a different LED layout, copy the matching profile into the printer configuration and edit its
`leds:` selectors consistently with `params_led_logo_idx` / `params_led_nozzle_idx`. The default is
three pixels (logo 1, nozzle 2–3). The `params_led_logo_idx` and `params_led_nozzle_idx` values are
comma-separated integer lists, for example a rainbow barf logo `1,2,3,4,5,6,7,8` and nozzle `9,10`.
Configure the tool's `chain_count` and `color_order` for the actual LEDs as well. If multiple tools share one
physical chain, assign explicit, nonoverlapping pixel selectors to each tool in every effect,
including its whole-chain `startup` and `critical_error` effects.

Define motion sensors with their tool's `extruder:` name. Empty sensor lists and absent LED names
are allowed. Multiple tools must not share an extruder or a tool-specific sensor name.

Klippain does not set standby temperatures or preheat unused tools. Slicer M104/M109 Tn commands
route to the requested tool, including tools that are not mounted. M109's low thermal inertia
workaround waits on the correct heater. Native M106/M107 routing and fan transfer are supplied by
upstream Toolchanger; use `[fan_generic]` tool fans, not a conflicting `[fan]`.

```ini
[gcode_macro _USER_VARIABLES]
variable_toolchanger_purge_on_change: False
variable_toolchanger_wipe_on_change: False
```

The two options are independent and disabled by default. If enabled, the normal Klippain purge
bucket/brush settings must also be enabled and calibrated. Cleanup runs only for actual changes
while printing, after the backend restores print coordinates, and returns to the saved print position.
Maintenance changes for probing/startup/end do not trigger cleanup. Initial START_PRINT purge/clean
remain controlled by the normal start action list.

## Automatic toolchange retraction and return

Toolchanger support retracts the outgoing, detected tool before a real exchange when it can safely
extrude. This is enabled by default with `retract_length` (20 mm by default) at 2100 mm/min. It
preserves the slicer's E position, extrusion mode, speed and flow factors. Same-tool selections,
initialization and cold outgoing tools do not trigger a retraction. Set
`variable_toolchanger_retract_on_change: False` to disable new toolchange retractions.

Klippain records each completed toolchange retraction by physical tool number and automatically
returns that exact amount the next time the tool is safely selected for use. It restores after a
successful slicer tool change when the tool is already at a safe extrusion temperature, after a
selected-tool M109 wait, before a selected tool's first prime-line pressure move, and before
RESUME. M104 stays asynchronous for parked tools; M104 for a selected tool with recorded retraction
waits for its requested safe temperature before returning filament. Cold outgoing tools are never
heated or extruded just to retract them. A cold pending return remains recorded until a later safe
use. Real exchanges during probing still retract a hot outgoing tool; probing and maintenance
selections do not trigger debt repayment.

The existing 23 mm prime-line pressure move and the END_PRINT/CANCEL_PRINT retract remain separate
Klippain behaviors; they are not counted as toolchange-retraction debt. The ledger tracks completed
toolchange retractions for the current Klipper runtime and is retained across toolchange failures
and manual recovery. It is cleared by a Klipper restart, so account for a pending retraction
manually if the printer restarts before that tool is safely used again.

## End, cancel and manual recovery

END_PRINT and CANCEL_PRINT select T0 **before PARK**, retraction, cooling or motor shutdown.
They do not automatically unload every tool. Selecting T0 must succeed before the rest of shutdown
continues; after an unresolved error, manually mount T0 before cancelling.

On a tool-change failure:

1. Report the previous and target tool numbers and request manual installation of the target tool.
2. Move to the failed tool's physical `params_safe_y` clearance plane. A relative delta reaches that
   physical position even if a user/tool coordinate offset is present. The speed is controlled by
   `variable_toolchanger_error_retreat_speed` (default 50 mm/s).
3. Run PAUSE, then the usual PARK. Error parking suppresses extrusion (`E=0`) because the mounted
   tool is not yet confirmed. Tool filament sensors are disabled and LEDs indicate an error.
4. The user installs the target tool and presses RESUME. Detection must match the target. The
   integration recognizes it with INITIALIZE_TOOLCHANGER and reapplies its settings without
   retrying docking. After the tool heats, Klippain restores the backend's saved prechange motion
   state through the confirmed target tool transform, repairs the native PAUSE_STATE vector while
   retaining its original modes and flow/speed factors, and continues through native RESUME. This
   corrects the upstream recovery's XYZ offset and E-origin mismatch without an automatic
   docking or recovery movement script.

The safe-Y path must be clear on your machine. If XYZ is not homed or safe Y lies outside travel,
the error handler pauses without retreating or parking. If position was lost (for example by idle
motor shutdown), RESUME is refused; cancel and rehome. A wrong manually mounted tool also leaves the
print paused.

A failed standalone slicer Tn command completes its paused virtual-SD line, so RESUME does not repeat
that exchange. A failure inside a larger startup/finish macro must abort that macro to suppress its
remaining moves. Klipper retains that virtual-SD line: after manual recovery, it runs the enclosing
START_PRINT/END_PRINT again. A manually invoked macro must instead be invoked again by the operator.

Happy Hare simultaneous operation is intentionally rejected for now. The Toolchanger feature flag,
profile and helper namespace are separate from the MMU path so a later backend-composition design
can add coexistence without conflating physical tools with filament gates.

## Development validation

Run the existing and new regression tests with Klipper's pinned template engine:

```bash
python3 -m venv /tmp/klippain-tests
/tmp/klippain-tests/bin/python -m pip install -r tests/requirements.txt
/tmp/klippain-tests/bin/python -B -m unittest discover -s tests -v
bash tests/install_mcu_templates_test.sh
python3 -B scripts/validate_probe_framework.py --repo .
```

`tests/toolchanger_harness.py` models Klipper's snapshot rendering and executes the actual macros
against recorded hardware primitives. It covers tool counts, homing order, failure retreat/pause,
manual resume, active heaters, cleanup, PA/shaper/sensors/LEDs and single-tool compatibility.

For native configuration checks, use disposable upstream checkouts, install Klipper's own
`klippy-requirements.txt`, link the upstream Toolchanger extensions, and build a Linux
MCU dictionary from the same Klipper revision. Run `tests/run_toolchanger_klippy_smoke.py --help` for the native runner. It exercises
the real Klippy loader and tool/temperature/profile operations in debug-output mode. Physical docking,
probe contact behavior, thermistor/pin polarity and slicer UI expansion still require printer/slicer
validation. No cloud test can certify those mechanical settings.
