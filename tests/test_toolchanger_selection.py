from __future__ import annotations

import re
import tempfile
import unittest

from toolchanger_config_helpers import ROOT, install_tree, read_includes
from toolchanger_harness import Machine, MacroError


class ToolSelectionTest(unittest.TestCase):
    def test_each_shipped_selector_loads_only_its_real_tools(self):
        with tempfile.TemporaryDirectory(prefix="klippain-selection-") as tmp:
            directory = install_tree(tmp)
            for count in range(2, 8):
                with self.subTest(count=count):
                    parsed, sources = read_includes(directory / f"toolchanger/tools_{count}.cfg")
                    self.assertEqual({f"tool T{i}" for i in range(count)},
                                     {s for s in parsed.sections() if s.startswith("tool ")})
                    self.assertEqual({f"mcu tool{i}" for i in range(count)},
                                     {s for s in parsed.sections() if s.startswith("mcu ")})
                    expected_extruders = {"extruder"} | {f"extruder{i}" for i in range(1, count)}
                    self.assertEqual(expected_extruders,
                                     {s for s in parsed.sections() if re.fullmatch(r"extruder\d*", s)})
                    self.assertEqual({f"neopixel T{i}_leds" for i in range(count)},
                                     {s for s in parsed.sections() if s.startswith("neopixel ")})
                    self.assertEqual({f"10-T{i}.cfg" for i in range(count)},
                                     {p.name for p in sources if p.name.startswith("10-T")})
                    markers = {s for s in parsed.sections() if s.startswith("gcode_macro _KLIPPAIN_TOOLSET_")}
                    self.assertEqual({f"gcode_macro _KLIPPAIN_TOOLSET_{count}"}, markers)
                    for number in range(7):
                        effect_sections = [s for s in parsed.sections()
                                           if s.startswith(f"led_effect tc_t{number}_")]
                        self.assertEqual(number < count, bool(effect_sections),
                                         f"unexpected effects for T{number}: {effect_sections}")
                    for number in range(count):
                        self.assertEqual(str(number), parsed.get(f"tool T{number}", "tool_number"))
                        self.assertEqual(f"tool_probe T{number}", parsed.get(f"tool T{number}", "tool_probe"))
                        self.assertIn("REPLACE_WITH_TOOL_", parsed.get(f"mcu tool{number}", "serial"))

    def test_printer_template_offers_exactly_one_choice_per_supported_count(self):
        text = (ROOT / "user_templates/printer.cfg").read_text()
        choices = re.findall(r"^#\s*\[include toolchanger/tools_(\d+)\.cfg\]", text, re.MULTILINE)
        self.assertEqual([str(n) for n in range(2, 8)], choices)
        self.assertNotRegex(text, r"(?m)^\s*\[include toolchanger/")
        self.assertNotIn("[include toolchanger/*.cfg]", text)

    def test_selector_markers_reject_conflicts_and_count_mismatch_before_hardware(self):
        for markers, numbers in [([2, 3], [0, 1, 2]), ([3], [0, 1]), ([2], [0, 2])]:
            with self.subTest(markers=markers, numbers=numbers):
                m = Machine(3)
                m.tc.update(tool_numbers=numbers, tool_names=[f"tool T{n}" for n in numbers])
                for count in markers:
                    m.printer[f"gcode_macro _KLIPPAIN_TOOLSET_{count}"] = {}
                with self.assertRaises(MacroError):
                    m.run("_INIT_CHECK_TOOLCHANGER")
                self.assertEqual(["_INIT_CHECK_TOOLCHANGER"], m.names())

    def test_explicit_custom_sparse_tools_remain_supported_without_selector(self):
        m = Machine(3)
        m.tc.update(tool_numbers=[0, 2], tool_names=["tool T0", "tool T2"])
        m.run("_INIT_CHECK_TOOLCHANGER\nM104 T2 S235\nT2")
        self.assertEqual(2, m.tc["tool_number"])
        self.assertEqual(235, m.printer["extruder2"]["target"])

    def test_invalid_number_sets_fail_before_hardware(self):
        for numbers in [[0], list(range(8)), [1, 2], [0, 7], [0, -1]]:
            with self.subTest(numbers=numbers):
                m = Machine(3)
                m.tc.update(tool_numbers=numbers, tool_names=[f"tool T{n}" for n in numbers])
                with self.assertRaises(MacroError):
                    m.run("_INIT_CHECK_TOOLCHANGER")
                self.assertEqual(["_INIT_CHECK_TOOLCHANGER"], m.names())


if __name__ == "__main__":
    unittest.main()
