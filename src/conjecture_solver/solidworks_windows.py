"""Experimental SOLIDWORKS COM adapter for self-contained parts.

The optional pywin32 dependency belongs to the selected Windows interpreter.
Native SOLIDWORKS commissioning is required; mock tests establish no CAD accuracy.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path


def describe():
    return {
        "application": "solidworks",
        "project_suffixes": [".sldprt"],
        "automation": "COM via pywin32",
        "session": "owned desktop COM process",
        "operations": {
            "part.rebuild_export": {
                "arguments_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["dimensions_si", "exports"],
                    "properties": {
                        "dimensions_si": {
                            "type": "object",
                            "maxProperties": 100,
                            "propertyNames": {"type": "string", "minLength": 1, "maxLength": 256},
                            "additionalProperties": {"type": "number"},
                        },
                        "exports": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 3,
                            "uniqueItems": True,
                            "items": {"enum": ["step", "iges", "stl"]},
                        },
                    },
                },
                "arguments": {
                    "dimensions_si": "dimension names and finite SI values (metres/radians)",
                    "exports": "one or more of step, iges, stl",
                },
            },
        },
        "requirements": ["registered SOLIDWORKS COM server", "Windows Python with pywin32"],
        "qualification": "experimental adapter; no native SOLIDWORKS commissioning claimed",
    }


def load_com():
    if sys.platform != "win32":
        raise RuntimeError("The SOLIDWORKS adapter requires Windows Python")
    try:
        import pythoncom
        import win32com.client
    except ImportError as error:
        raise RuntimeError("Install pywin32 in the selected Windows Python interpreter") from error
    return pythoncom, win32com.client


def check(installation):
    pythoncom, _ = load_com()
    executable = Path(installation) / "SLDWORKS.exe"
    if not executable.is_file():
        raise ValueError("Choose the SOLIDWORKS directory containing SLDWORKS.exe")
    return {
        "interfaces_loaded": True,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "installation": installation,
        "com_class": str(pythoncom.CLSIDFromProgID("SldWorks.Application")),
        "application_started": False,
        "model_tested": False,
    }


def input_sources(project, script=None):
    project = Path(project)
    if script is not None:
        raise ValueError("This SOLIDWORKS operation does not accept a native script")
    if project.suffix.lower() != ".sldprt" or not project.is_file():
        raise ValueError("Supply a self-contained SOLIDWORKS .sldprt part")
    return {"inputs/project.sldprt": project}


def validate(operation, arguments, inputs):
    if operation != "part.rebuild_export" or not isinstance(arguments, dict):
        raise ValueError("Unsupported SOLIDWORKS operation")
    if set(arguments) != {"dimensions_si", "exports"}:
        raise ValueError("Supply dimensions_si and exports")
    dimensions = arguments["dimensions_si"]
    if not isinstance(dimensions, dict) or len(dimensions) > 100:
        raise ValueError("Invalid SOLIDWORKS dimensions")
    for name, number in dimensions.items():
        if not isinstance(name, str) or not name or len(name) > 256 or "\x00" in name:
            raise ValueError("Invalid SOLIDWORKS dimension name")
        if type(number) not in (float, int) or not math.isfinite(number):
            raise ValueError("Dimension values must be finite SI numbers")
    exports = arguments["exports"]
    if (
        not isinstance(exports, list)
        or not exports
        or any(name not in ("step", "iges", "stl") for name in exports)
        or len(set(exports)) != len(exports)
    ):
        raise ValueError("Choose distinct step, iges or stl exports")
    if set(inputs) != {"inputs/project.sldprt"}:
        raise ValueError("Only a self-contained part is supported; assemblies are not staged")


def execute(job, request, context):
    pythoncom, client = load_com()
    pythoncom.CoInitialize()
    application = None
    model = active = dimension = None
    owned = False
    try:
        # COM activation may occur outside our descendant tree. Claim the new PID
        # before opening a model. An existing operator session must be refused.
        executable = Path(request["installation"]) / "SLDWORKS.exe"
        started = context["begin_external_activation"](executable)
        application = client.DispatchEx("SldWorks.Application")
        pid = application.GetProcessID()
        context["claim_process"](pid, started, executable)
        owned = True
        application.Visible = False
        application.UserControl = False

        def integer():
            return client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)

        errors, warnings = integer(), integer()
        project = job / "work/project.sldprt"
        application.SetCurrentWorkingDirectory(str(project.parent))
        model = application.OpenDoc6(str(project), 1, 1, "", errors, warnings)
        if model is None or errors.value:
            raise RuntimeError("SOLIDWORKS failed to open the part: " + str(errors.value))
        open_warnings = warnings.value
        observed = {}
        for name, number in request["arguments"]["dimensions_si"].items():
            dimension = model.Parameter(name)
            if dimension is None:
                raise ValueError("Unknown SOLIDWORKS dimension: " + name)
            dimension.SystemValue = number
        if not model.ForceRebuild3(False):
            raise RuntimeError("SOLIDWORKS part rebuild failed")
        for name, expected in request["arguments"]["dimensions_si"].items():
            actual = model.Parameter(name).SystemValue
            if not math.isfinite(actual) or not math.isclose(actual, expected, rel_tol=1e-12):
                raise ValueError("Rebuilt dimension differs from request: " + name)
            observed[name] = actual
        errors, warnings = integer(), integer()
        if not model.Save3(1, errors, warnings) or errors.value:
            raise RuntimeError("SOLIDWORKS failed to save the working part")
        activation = integer()
        active = application.ActivateDoc3(model.GetTitle(), False, 0, activation)
        if active is None or activation.value:
            raise RuntimeError("SOLIDWORKS failed to activate the part for export")
        active.ClearSelection2(True)
        exported = []
        for extension in request["arguments"]["exports"]:
            target = project.with_suffix("." + extension)
            errors, warnings = integer(), integer()
            if not active.Extension.SaveAs(str(target), 0, 1, None, errors, warnings):
                raise RuntimeError("SOLIDWORKS export failed: " + extension)
            if errors.value or not target.is_file() or target.stat().st_size == 0:
                raise RuntimeError("SOLIDWORKS did not produce a valid export: " + extension)
            exported.append(target.relative_to(job).as_posix())
        application.CloseDoc(model.GetTitle())
        return {
            "dimensions_si": observed,
            "exports": exported,
            "solidworks_runtime": {"pid": pid, "revision": application.RevisionNumber()},
            "open_warnings": open_warnings,
        }
    finally:
        try:
            if owned:
                application.ExitApp()
        finally:
            model = active = dimension = None
            application = None
            pythoncom.CoUninitialize()
