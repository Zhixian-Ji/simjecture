"""Material EOS builder CLI; no material or state domain is selected implicitly."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from .builder import build
from .profiles import PROFILE_ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version="material-eos 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("profiles", help="List explicit model recipes")
    command = commands.add_parser(
        "build", help="Generate a model table and EOS-only IONMIX4 export"
    )
    command.add_argument("--profile", required=True)
    command.add_argument(
        "--grid", required=True, help="JSON arrays density_g_cm3 and temperature_K"
    )
    command.add_argument("--output", required=True, help="New output directory")
    command.add_argument("--database", help="Explicit native FEOS material database")
    command.add_argument("--material-id", type=int)
    command.add_argument(
        "--allow-unstable",
        action="store_true",
        help="Research export retaining failed domain checks",
    )
    command.add_argument(
        "--coarsen-temperature-grid",
        action="store_true",
        help="Remove columns only to repair serialization-induced flat energy intervals",
    )
    args = parser.parse_args(argv)
    if args.command == "profiles":
        print(
            json.dumps(
                [json.loads(p.read_text()) for p in sorted(PROFILE_ROOT.glob("*.json"))], indent=2
            )
        )
        return 0
    try:
        metadata = build(
            args.profile,
            args.grid,
            args.output,
            database=args.database,
            material_id=args.material_id,
            allow_unstable=args.allow_unstable,
            coarsen_temperature=args.coarsen_temperature_grid,
        )
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"completed": False, "error": str(error)}), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "completed": True,
                "output": args.output,
                "profile": metadata["profile"]["id"],
                "checks": metadata["checks"],
                "physical_validation": metadata["physical_validation"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
