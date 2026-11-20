"""Install-like toolchanger configurations for static and real Klippy tests.

The optional directory is copied just as documented. Managed directories remain
symlinks, so relative includes are exercised at the user's configuration path.
"""
from __future__ import annotations

import configparser
import glob
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install_tree(directory, *, mechanism="stealthchanger", simulated=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in ["config", "macros"]:
        (directory / name).symlink_to(ROOT / name, target_is_directory=True)
    shutil.copytree(ROOT / "user_templates/toolchanger", directory / "toolchanger")
    common = directory / "toolchanger/00-toolchanger.cfg"
    text = common.read_text()
    text = text.replace("[include ../config/hardware/toolchanger/stealthchanger.cfg]",
                        f"[include ../config/hardware/toolchanger/{mechanism}.cfg]")
    if simulated:
        text = text.replace("params_docks_calibrated: False", "params_docks_calibrated: True")
    common.write_text(text)
    if simulated:
        # Only MCU wiring is translated for the Linux debug dictionary; tool
        # names, numbering, peripherals, macros, and include paths stay real.
        pins = ["PD0", "PD1", "PD2", "PA15", "PB13", "PA3", "PA0", "PA1",
                "PB6", "PB5", "PD3"]
        for tool_file in (directory / "toolchanger").glob("10-T*.cfg"):
            text = tool_file.read_text()
            for index, pin in enumerate(pins):
                text = re.sub(r"\b" + pin + r"\b",
                              "analog0" if pin == "PA3" else f"gpio{index}", text)
            tool_file.write_text(text)
    return directory


def read_includes(path):
    """Read a Klipper-style include tree, retaining its lexical symlink paths.

    Native smoke tests also parse these files with Klipper itself. This small
    independent reader permits assertions about absent sections and sources.
    """
    parser = configparser.RawConfigParser(
        strict=False, interpolation=None, inline_comment_prefixes=("#", ";"))
    sources = []
    visiting = set()

    def read(source):
        source = Path(source)
        key = source.absolute()
        if key in visiting:
            raise ValueError(f"Recursive config include: {source}")
        visiting.add(key)
        sources.append(source)
        block = []

        def flush():
            if block:
                parser.read_string("\n".join(block), source=str(source))
                block.clear()

        for line in source.read_text().splitlines():
            stripped = line.split("#", 1)[0]
            include = re.fullmatch(r"\s*\[include\s+([^]]+)\]\s*", stripped)
            if include:
                flush()
                pattern = str(source.parent / include.group(1).strip())
                matches = sorted(glob.glob(pattern))
                if not matches and not glob.has_magic(pattern):
                    raise FileNotFoundError(pattern)
                for match in matches:
                    read(match)
            else:
                block.append(stripped)
        flush()
        visiting.remove(key)

    read(path)
    return parser, sources
