"""Install the material builder separately, reusing the pinned FEOS source bootstrap."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    root, prefix = args.project_root.resolve(), args.prefix.resolve()
    scripts = root / "skills/eos/scripts"
    command = [
        sys.executable,
        str(scripts / "bootstrap_feos.py"),
        "--prefix",
        str(prefix),
        "--project-root",
        str(root),
        "--jobs",
        str(args.jobs),
    ]
    if args.source:
        command += ["--source", str(args.source)]
    subprocess.run(command, check=True)
    code = prefix / "build/FEOS/Code"
    bridge = scripts / "feos_tf_bridge.cpp"
    (prefix / "lib").mkdir(exist_ok=True)
    sources = [
        code / n
        for n in [
            "COMMON-01_UTILITIES.C",
            "COMMON-02_READFILE.C",
            "LIB-01_TF_SERVICES.C",
            "LIB-02_TF_TABLE_1.C",
            "LIB-02_TF_TABLE_2.C",
        ]
    ]
    compiler = os.environ.get("CXX", "g++")
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-O2",
            "-fPIC",
            "-shared",
            "-pthread",
            "-I" + str(code),
            str(bridge),
            *map(str, sources),
            "-o",
            str(prefix / "lib/libfeos-tf.so"),
        ],
        check=True,
    )
    package = root / "src/conjecture_solver/material_eos"
    target = prefix / "share/material_eos"
    shutil.copytree(
        package, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    launcher = prefix / "bin/material-eos-builder"
    launcher.write_text(
        "#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\n"
        "root=Path(__file__).resolve().parents[1]\n"
        'sys.path.insert(0,str(root/"share"))\n'
        'os.environ.setdefault("FEOS_QUERY",str(root/"bin/feos-query"))\n'
        'os.environ.setdefault("FEOS_DATA",str(root/"share/feos"))\n'
        'os.environ.setdefault("FEOS_TF_LIBRARY",str(root/"lib/libfeos-tf.so"))\n'
        "from material_eos.__main__ import main\nraise SystemExit(main())\n"
    )
    launcher.chmod(0o755)
    record_path = prefix / "share/build-record.json"
    record = json.loads(record_path.read_text())
    files = [p for p in target.rglob("*") if p.is_file()]
    record.update(
        material_builder_version="0.1.0",
        material_builder_files={
            str(p.relative_to(prefix)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
        },
        raw_tf_adapter_sha256=hashlib.sha256(bridge.read_bytes()).hexdigest(),
        python=sys.version,
        numerical_versions={name: __import__(name).__version__ for name in ("numpy", "scipy")},
    )
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    print(f"Material EOS builder ready at {prefix}")


if __name__ == "__main__":
    main()
