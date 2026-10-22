"""Install the checksummed Frankfurt FEOS 16.7 source; no material qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

SOURCE_URL = (
    "https://data.mendeley.com/public-files/datasets/6vjsv6v48p/"
    "files/902b7aaf-1643-4145-b391-dc81830c23fd/file_downloaded"
)
SOURCE_SHA256 = "01c2e0d9e3d5c787418405bf9dd3bc5beec28566d4bdb7c53cffbabf756b5eb4"
TREE_SHA256 = "1cf8bfc48578889fe1b54e6ea80e1ec16ad73d7e3560dba5da2811c1c089c6ce"


def source_inventory(source):
    inventory = {}
    for folder in ("Code", "EOS-Data", "Documents"):
        for path in sorted((source / folder).iterdir()):
            if path.is_symlink():
                raise ValueError("FEOS source may not contain symlinks")
            if path.suffix in {".o", ".a"} or path.name in {"feos", "showeos"}:
                continue
            if not path.is_file():
                raise ValueError("Unexpected FEOS source entry")
            inventory[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if digest != TREE_SHA256:
        raise ValueError("FEOS source tree does not match the published, pinned archive")
    return inventory


def acquire_source(prefix, supplied=None):
    if supplied:
        source = Path(supplied).resolve(strict=True)
        inventory = source_inventory(source)
    else:
        archive = prefix / "src/FEOS.tar.gz"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if (
            not archive.is_file()
            or hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256
        ):
            request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "Simjecture-FEOS"})
            with urllib.request.urlopen(request, timeout=90) as response:
                data = response.read(8 * 1024**2 + 1)
            if len(data) > 8 * 1024**2 or hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
                raise ValueError("FEOS archive checksum or size mismatch; source was not executed")
            archive.write_bytes(data)
        with tarfile.open(archive) as bundle:
            for member in bundle:
                if not (member.isdir() or member.isfile()):
                    raise ValueError("Unexpected non-regular entry in FEOS archive")
                if Path(member.name).is_absolute() or ".." in Path(member.name).parts:
                    raise ValueError("Unsafe path in FEOS archive")
            bundle.extractall(prefix / "src", filter="data")
        source = prefix / "src/FEOS"
        inventory = source_inventory(source)
    return source, inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("jobs must be positive")
    prefix = args.prefix.resolve()
    source, inventory = acquire_source(prefix, args.source)
    # Never build into an operator-supplied source tree.
    build = prefix / "build/FEOS"
    shutil.copytree(source, build, dirs_exist_ok=True)
    compiler = os.environ.get("CXX", "g++")
    subprocess.run(
        ["make", "-B", "-C", str(build / "Code"), "-j", str(args.jobs),
         f"CXX={compiler}", "CFLAGS=-O2", "libfeos.a"], check=True,
    )
    driver = args.project_root / "skills/eos/scripts/feos_query.cpp"
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [compiler, "-std=c++17", "-O2", str(driver), str(build / "Code/libfeos.a"),
         "-I" + str(build / "Code"), "-o", str(prefix / "bin/feos-query")], check=True,
    )
    data = prefix / "share/feos"
    data.mkdir(parents=True, exist_ok=True)
    for name in ("FEOS_TF-Table_1197.dat", "FEOS_Material-DB.dat"):
        shutil.copy2(source / "EOS-Data" / name, data / name)
    for name in ("License.txt", "Info.txt", "FEOS-Package-Documentation.pdf"):
        shutil.copy2(source / "Documents" / name, data / name)
    record = dict(
        package="Frankfurt FEOS", version="16.7", source_doi="10.17632/6vjsv6v48p.1",
        source_url=SOURCE_URL, source_sha256=SOURCE_SHA256, source_tree_sha256=TREE_SHA256,
        source_files=inventory, license="GPL-3.0-or-later", source_modified=False,
        driver_sha256=hashlib.sha256(driver.read_bytes()).hexdigest(),
        compiler=subprocess.check_output([compiler, "--version"], text=True).splitlines()[0],
        adapter="FEOS_Get_EOS_All; homogeneous EOS; no Maxwell or soft-sphere replacement",
    )
    (prefix / "share/build-record.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"Frankfurt FEOS 16.7 runtime ready at {prefix}")


if __name__ == "__main__":
    main()
