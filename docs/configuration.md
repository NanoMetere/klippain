# Configuration of Klippain

Klippain requires a few simple steps to configure and customize it for your printer: please follow the following documentation step by step in order to get your printer running.

  > **Warning**:
  >
  > General rule to keep the auto-update feature working: **never modify Klippain files directly**, but instead add overrides as per the following documentation. To proceed, you can modify all the pre-installed templates in your config root folder (`printer.cfg`, `mcu.cfg`, `variables.cfg` and `overrides.cfg` and, if applicable, the MMU configs files in the `mmu` directory created when you install Happy-Hare) as they will be preserved on update.

## 1. MCU Settings

Before configuring Klippain, you will need to configure your MCUs and wiring. If you didn't select any templates during the installation (or want to customize the default wiring), modify your `mcu.cfg` file according to the [MCU pinout and wiring documentation](./pinout.md).

Don't forget to fill in the serial_port or can_uuid of your MCUs. Refer to the [official Klipper documentation](https://www.klipper3d.org/FAQ.html#wheres-my-serial-port) for help.

## 2. Printer Settings, Overrides, and Variables

Don't overlook, this section is the most important. Now that your MCU is configured, you will need to follow some additionnal steps to configure Klippain:

  1. In `printer.cfg`, uncomment lines corresponding to your printer hardware or software components to enable them (e.g., extruder type, XY motors, Z motors, QGL vs Z_TILT, etc.).
  1. Then, edit `overrides.cfg` according to the [overrides documentation and examples](./overrides.md). Use overrides to tweak machine dimensions, invert motor directions, change axis limits, currents, sensors type, or anything you feel the need to change.
  1. Once Klipper boots successfully, adjust the `variables.cfg` file to match your machine's configuration. This file provides additional customization for macro behavior (coordinates, enabling/disabling software features, etc.).

  > **Note**:
  >
  > If you want to use an MMU/ERCF with Klippain, you need to install the [HappyHare](https://github.com/moggieuk/Happy-Hare) backend. Also, have a look at the Klippain [MMU documentation](./docs/mmu.md) for more specific details about its configuration.

For physical tool changers, follow the [Toolchanger configuration guide](./toolchanger.md). It covers StealthChanger/TapChanger fixed docks, per-tool wiring, T0-only probing and the optional first-home-with-any-tool mode.

## 3. Initial startup of the machine

Before your first print, **carefully check all features to prevent issues on your machine!** Begin with the [config checks section from the official Klipper documentation](https://www.klipper3d.org/Config_checks.html).

Next, ensure the mechanical probe (if used) can be attached/detached, verify that QGL/Z_TILT works, and confirm correct coordinates for all components (purge bucket, physical Z endstop, etc.). Check your first layer calibration (and the `switch_offset` parameter of the automatic Z calibration plugin, if used), etc...

## 4. Slicer configuration

Klippain will work out of the box with most slicers on the market and your profiles should be almost ready to go. You will just need to set some custom start print and end print gcodes to give Klippain the correct info.

### Custom print start Gcode

The table below is the standard single-tool/MMU setup. Toolchanger profiles should use the separate
`Tn_TEMP` commands and examples below.

| Slicer | Custom start print gcode |
|:-------|:-------------------------|
|[SuperSlicer](https://github.com/supermerill/SuperSlicer)|`START_PRINT EXTRUDER_TEMP={first_layer_temperature[initial_extruder] + extruder_temperature_offset[initial_extruder]} BED_TEMP=[first_layer_bed_temperature] MATERIAL={filament_type[initial_extruder]} SIZE={first_layer_print_min[0]}_{first_layer_print_min[1]}_{first_layer_print_max[0]}_{first_layer_print_max[1]} INITIAL_TOOL={initial_extruder}`|
|[OrcaSlicer](https://github.com/SoftFever/OrcaSlicer)|`START_PRINT EXTRUDER_TEMP={nozzle_temperature_initial_layer[initial_tool]} BED_TEMP=[bed_temperature_initial_layer_single] MATERIAL={filament_type[initial_tool]} SIZE={first_layer_print_min[0]}_{first_layer_print_min[1]}_{first_layer_print_max[0]}_{first_layer_print_max[1]} INITIAL_TOOL=[initial_tool]`|
|[PrusaSlicer](https://github.com/prusa3d/PrusaSlicer)|`START_PRINT EXTRUDER_TEMP={first_layer_temperature[initial_extruder]} BED_TEMP=[first_layer_bed_temperature] MATERIAL={filament_type[initial_extruder]} SIZE={first_layer_print_min[0]}_{first_layer_print_min[1]}_{first_layer_print_max[0]}_{first_layer_print_max[1]} INITIAL_TOOL={initial_extruder}`|

For a Toolchanger, `INITIAL_TOOL` is the first **physical tool**, not an MMU gate. The temperature
and material above are indexed by that initial tool; do not pass a multi-value temperature list.
Klippain performs all probing on T0, then selects `INITIAL_TOOL` during `extruder_heating` before
purge/primeline. Keep all probing actions before that action. Slicer-generated `T0`–`T6` commands
perform later changes. Leave unused-tool/standby temperature control in the slicer; Klippain routes
`M104 Tn` / `M109 Tn` to the correct hotend without inventing a standby temperature.

Toolchanger `START_PRINT` accepts scalar `T0_TEMP` through `T6_TEMP` parameters. A positive value marks that
physical tool as used and gives its first-layer nozzle target; a missing or zero value marks it
unused. Include every tool used by the job, including `INITIAL_TOOL`, whenever you use this mode.
The initial tool's `EXTRUDER_TEMP` must match its `Tn_TEMP` target. T0 can still receive the
configured safe probing temperature even if it is not listed as used. If no `Tn_TEMP` parameters are
present, Klippain keeps the legacy one-tool behavior: only `INITIAL_TOOL` and `EXTRUDER_TEMP` are
used.

By default Klippain primes all tools declared by the `Tn_TEMP` values, one line per tool, with
`INITIAL_TOOL` last. Set `variable_toolchanger_prime_all_used_tools: False` in
`variables.cfg` to prime only `INITIAL_TOOL`; the full used-tool temperature map is still retained
for optional preheating. `variable_toolchanger_preheat_used_tools` defaults to `False`, so bulk
preheating of parked used tools is off: the initial tool is heated and waited for during startup, and
each other tool is heated and waited for at its primeline. If a tool was not primed or preheated,
the slicer must set its target and issue a selected-tool `M109` wait before its first extrusion.
Set preheating to `True` to start declared used non-T0 hotends
heating asynchronously during early preheating. After probing completes, the final heating action
starts all declared used-tool targets, including T0 if it is listed. T0 stays at its safe probing
temperature until probing completes. Even with all-tool priming and bulk preheat off, each
tool is heated and waited for in turn before its line is extruded. Keep standby targets in slicer
tool-change/temperature commands; do not pass standby temperatures as `Tn_TEMP`.

The examples below show all seven tool indices. Before copying one, remove every conditional above
the highest installed physical tool index (T0–T1 for 2 consecutive tools, through T0–T6 for 7); for
a sparse tool set, keep only conditionals for installed physical tool numbers. This avoids slicer
errors from references to unconfigured indices. The remaining conditionals ensure that the exported
line contains only tools used by that print. Use one filament profile per physical tool, then inspect
the exported `START_PRINT` line: targets must be scalar numbers and the initial tool must have a
positive target.

**Toolchanger START_PRINT commands**

**OrcaSlicer** (seven-tool template; trim unconfigured indices first. Uses `initial_tool`,
`is_extruder_used[]`, and first-layer nozzle targets):

```text
START_PRINT EXTRUDER_TEMP={nozzle_temperature_initial_layer[initial_tool]} BED_TEMP=[bed_temperature_initial_layer_single] MATERIAL={filament_type[initial_tool]} SIZE={first_layer_print_min[0]}_{first_layer_print_min[1]}_{first_layer_print_max[0]}_{first_layer_print_max[1]} INITIAL_TOOL=[initial_tool]{if is_extruder_used[0]} T0_TEMP={nozzle_temperature_initial_layer[0]}{endif}{if is_extruder_used[1]} T1_TEMP={nozzle_temperature_initial_layer[1]}{endif}{if is_extruder_used[2]} T2_TEMP={nozzle_temperature_initial_layer[2]}{endif}{if is_extruder_used[3]} T3_TEMP={nozzle_temperature_initial_layer[3]}{endif}{if is_extruder_used[4]} T4_TEMP={nozzle_temperature_initial_layer[4]}{endif}{if is_extruder_used[5]} T5_TEMP={nozzle_temperature_initial_layer[5]}{endif}{if is_extruder_used[6]} T6_TEMP={nozzle_temperature_initial_layer[6]}{endif}
```

**PrusaSlicer** (seven-tool template; trim unconfigured indices first. Uses `initial_extruder`,
`is_extruder_used[]`, and first-layer temperatures):

```text
START_PRINT EXTRUDER_TEMP={first_layer_temperature[initial_extruder]} BED_TEMP=[first_layer_bed_temperature] MATERIAL={filament_type[initial_extruder]} SIZE={first_layer_print_min[0]}_{first_layer_print_min[1]}_{first_layer_print_max[0]}_{first_layer_print_max[1]} INITIAL_TOOL={initial_extruder}{if is_extruder_used[0]} T0_TEMP={first_layer_temperature[0]}{endif}{if is_extruder_used[1]} T1_TEMP={first_layer_temperature[1]}{endif}{if is_extruder_used[2]} T2_TEMP={first_layer_temperature[2]}{endif}{if is_extruder_used[3]} T3_TEMP={first_layer_temperature[3]}{endif}{if is_extruder_used[4]} T4_TEMP={first_layer_temperature[4]}{endif}{if is_extruder_used[5]} T5_TEMP={first_layer_temperature[5]}{endif}{if is_extruder_used[6]} T6_TEMP={first_layer_temperature[6]}{endif}
```

**SuperSlicer** has no used-extruder conditional in its current placeholders. For every job, list
exactly its used physical tools and update that list when the job changes. This complete example is
for a job using T0 and T3; change the explicit parameters to match the sliced job:

```text
START_PRINT EXTRUDER_TEMP={first_layer_temperature[initial_extruder] + extruder_temperature_offset[initial_extruder]} BED_TEMP=[first_layer_bed_temperature] MATERIAL={filament_type[initial_extruder]} SIZE={first_layer_print_min[0]}_{first_layer_print_min[1]}_{first_layer_print_max[0]}_{first_layer_print_max[1]} INITIAL_TOOL={initial_extruder} T0_TEMP={first_layer_temperature[0] + extruder_temperature_offset[0]} T3_TEMP={first_layer_temperature[3] + extruder_temperature_offset[3]}
```

Keep each slicer's normal tool-change commands. If adding custom preheating, use its next-tool
placeholder (for example `M104 T[next_extruder] S{temperature[next_extruder]}` where supported).
Inspect the exported G-code to confirm the initial index, a scalar initial temperature/material,
and one Tn selection per exchange; do not duplicate a selection already emitted by the slicer.
Toolchanger `END_PRINT` and `CANCEL_PRINT` return to T0 before parking. See the
[recovery and per-tool settings guide](./toolchanger.md) for the remaining setup.

In addition, there are a few other optional parameters that are supported in Klippain (they must be added on the same line after the first parameters):
  - `CHAMBER=[chamber_temperature]` to set a target heatsoak temperature during the START_PRINT sequence.
  - `TOTAL_LAYER=[total_layer_count]` to be able to set the PRINT_STATS_INFOS in Klipper. If you use this, you will also need to add the corresponding `SET_PRINT_STATS_INFO CURRENT_LAYER={layer_num}` to your slicer custom layer change gcode.
  - `TOOLS_USED=!referenced_tools!` *(only for MMU users)* is highly recommended to check only the used tools with the HappyHare [Moonraker gcode preprocessor](https://github.com/moggieuk/Happy-Hare/blob/main/doc/gcode_preprocessing.md).
  - `CHECK_GATES=0` or `1` *(only for MMU users)* that will override the corresponding variable defined in Klippain `variables.cfg` for this specific print.
  - `SYNC_MMU_EXTRUDER=1` *(only for MMU users)* if you want to stay with the default `sync_to_extruder: 0` value of HappyHare (defined in `mmu/mmu_parameters.cfg`), but still want to use the sync for a specific print.


### Custom print end Gcode

All slicers will be happy with a simple:
```
END_PRINT
```

In addition, there are a few other optional parameters that are supported in Klippain (they must be added on the same line after the first parameters):
  - `FILTER_TIME=600` that will override the corresponding variable defined in Klippain `variables.cfg` for this specific print. Time is expressed in seconds.
  - `MMU_UNLOAD_AT_END=0` or `1` *(only for MMU users)* that will override the corresponding variable defined in Klippain `variables.cfg`.
