"""Import a DXF into FreeCAD and report what came in (manual viewer check, H4-2).

Runs inside FreeCAD's own Python, not in the project venv. From ``sketch2cad-api/``::

    FC=/Applications/FreeCAD.app/Contents/Resources/bin
    CFG=$(mktemp -d)
    "$FC/freecadcmd" -u "$CFG/user.cfg" -s "$CFG/system.cfg" \\
        scripts/freecad_check_dxf.py --pass tests/golden/plan.dxf

``-u``/``-s`` point FreeCAD at throw-away preference files, so the DXF import preferences
this script sets never reach the user's own ``user.cfg``. An argument ending in ``.json`` gets
the report as JSON. With the GUI binary (``FreeCAD.app/Contents/MacOS/FreeCAD`` instead of
``freecadcmd``) an argument ending in ``.png`` also saves a top view screenshot, then the
window closes; see ``docs/dxf-viewers.md``.

The report lists the FreeCAD version, the objects per DXF layer and type, the texts, the
dimensions and the length in FreeCAD units (mm) of the ``L_CONTROL`` segment. Exit status 0
when the import produced objects and that segment measures ``CONTROL_SEGMENT_M`` metres.
"""

import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any

import FreeCAD  # type: ignore[import-not-found]  # provided by the FreeCAD interpreter

CONTROL_LAYER = "L_CONTROL"
CONTROL_SEGMENT_M = 5.0
MM_PER_M = 1000.0
LENGTH_TOLERANCE_MM = 0.01
DOC_NAME = "dxf_check"
# Draft preferences read by the DXF importer (Import.readDXF and importDXF).
IMPORT_PREFERENCES_BOOL = {
    "dxfUseLegacyImporter": False,
    "dxfShowDialog": False,
    "dxftext": True,  # texts and dimensions
    "dxflayout": False,  # model space only
    "dxfImportPoints": True,
    "dxfstarblocks": False,
    "dxfGetOriginalColors": True,
    "dxfUseDraftVisGroups": True,  # one Draft layer per DXF layer
    # "Individual part shapes", FreeCAD's default mode. The "Draft objects" mode aborts its
    # post-processing on this file in FreeCAD 1.1.4 ('FeaturePython' has no 'Placement').
    "dxfImportAsDraft": False,
    "dxfImportAsPrimitives": False,
    "dxfImportAsShapes": True,
    "dxfImportAsFused": False,
}
IMPORT_MODE_SHAPES = 2


def script_arguments() -> list[str]:
    argv = sys.argv
    return argv[argv.index("--pass") + 1 :] if "--pass" in argv else []


def set_import_preferences() -> None:
    group = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Draft")
    for name, value in IMPORT_PREFERENCES_BOOL.items():
        group.SetBool(name, value)
    group.SetInt("DxfImportMode", IMPORT_MODE_SHAPES)


def object_kind(obj: Any) -> str:
    proxy = getattr(obj, "Proxy", None)
    proxy_type = getattr(proxy, "Type", None)
    return f"{obj.TypeId}:{proxy_type}" if proxy_type else obj.TypeId


def layer_of(obj: Any, layers: dict[str, str]) -> str:
    """Draft layer holding ``obj``, else its ``OriginalLayer`` (block definitions: none)."""
    return layers.get(obj.Name) or getattr(obj, "OriginalLayer", None) or "(none)"


def draft_layers(doc: Any) -> dict[str, list[Any]]:
    found: dict[str, list[Any]] = {}
    for obj in doc.Objects:
        if getattr(getattr(obj, "Proxy", None), "Type", None) == "Layer":
            found[obj.Label] = list(obj.Group)
    return found


def edge_lengths(obj: Any) -> list[float]:
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return []
    return [edge.Length for edge in shape.Edges]


def text_of(obj: Any) -> str | None:
    for name in ("Text", "LabelText", "String"):
        value = getattr(obj, name, None)
        if value:
            return " ".join(value) if isinstance(value, list) else str(value)
    return None


def build_report(doc: Any, dxf_path: str) -> dict[str, Any]:
    layers = draft_layers(doc)
    layer_by_object = {member.Name: name for name, members in layers.items() for member in members}
    per_layer: dict[str, Counter[str]] = defaultdict(Counter)
    texts: list[dict[str, str]] = []
    dimensions: list[dict[str, Any]] = []
    control_lengths: list[float] = []
    for obj in doc.Objects:
        kind = object_kind(obj)
        if kind.endswith(":Layer") or kind.endswith(":LayerContainer"):
            continue
        layer = layer_of(obj, layer_by_object)
        per_layer[layer][kind] += 1
        content = text_of(obj)
        if content is not None:
            texts.append({"layer": layer, "kind": kind, "text": content})
        if "Dimension" in kind:
            view = getattr(obj, "ViewObject", None)  # None without the GUI
            dimensions.append(
                {
                    "layer": layer,
                    "distance": str(getattr(obj, "Distance", "")),
                    "override_text": getattr(view, "OverrideText", None) if view else None,
                    "font_size": str(getattr(view, "FontSize", "")) if view else None,
                }
            )
        if layer == CONTROL_LAYER:
            control_lengths.extend(round(length, 6) for length in edge_lengths(obj))
    control_lengths.sort()
    version = FreeCAD.Version()
    return {
        "freecad_version": ".".join(version[:3]),
        "freecad_revision": version[3],
        "dxf": dxf_path,
        "layers": sorted(layers),
        "objects_per_layer": {name: dict(counts) for name, counts in sorted(per_layer.items())},
        "texts": texts,
        "dimensions": dimensions,
        "control_edge_lengths_mm": control_lengths,
        "expected_control_length_mm": CONTROL_SEGMENT_M * MM_PER_M,
    }


def save_screenshot(png_path: str) -> None:
    import FreeCADGui  # type: ignore[import-not-found]

    view = FreeCADGui.activeDocument().activeView()
    view.viewTop()
    view.fitAll()
    FreeCADGui.updateGui()
    view.saveImage(png_path, 1600, 1000, "White")


def main() -> int:
    arguments = script_arguments()
    if not arguments:
        print("usage: freecadcmd freecad_check_dxf.py --pass PLAN.dxf [OUT.json] [SHOT.png]")
        return 2
    dxf_path = os.path.abspath(arguments[0])
    if not os.path.isfile(dxf_path):
        print(f"S2C_ERROR no such file: {dxf_path}")
        return 2
    json_path = next((arg for arg in arguments[1:] if arg.endswith(".json")), None)
    png_path = next((arg for arg in arguments[1:] if arg.endswith(".png")), None)
    set_import_preferences()
    import importDXF  # type: ignore[import-not-found]

    doc = FreeCAD.newDocument(DOC_NAME)
    importDXF.insert(dxf_path, doc.Name)
    doc.recompute()
    report = build_report(doc, dxf_path)
    expected = report["expected_control_length_mm"]
    report["control_segment_ok"] = any(
        abs(length - expected) <= LENGTH_TOLERANCE_MM
        for length in report["control_edge_lengths_mm"]
    )
    report["imported"] = bool(report["objects_per_layer"])
    report["gui"] = bool(FreeCAD.GuiUp)
    if png_path and FreeCAD.GuiUp:
        save_screenshot(png_path)
        report["screenshot"] = png_path
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print("S2C_REPORT " + text)  # the GUI binary prints to its report view, not stdout
    if json_path:
        with open(json_path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    FreeCAD.closeDocument(doc.Name)
    return 0 if report["imported"] and report["control_segment_ok"] else 1


try:
    status = main()
except Exception as error:  # freecadcmd itself exits 0 on uncaught errors
    print(f"S2C_ERROR {type(error).__name__}: {error}")
    status = 1
sys.stdout.flush()
if FreeCAD.GuiUp:
    # Quit from the event loop: closing the window or calling sys.exit inside this macro
    # aborts FreeCAD 1.1.4 on macOS. The GUI exit status is not the check result: read the
    # JSON report.
    import FreeCADGui  # type: ignore[import-not-found]
    from PySide import QtCore  # type: ignore[import-not-found]

    QtCore.QTimer.singleShot(0, FreeCADGui.getMainWindow().close)
else:
    sys.exit(status)
