#!/usr/bin/env bash
set -euo pipefail
prefix=""
project_root=""
source_tree=""
jobs=2
while (($#)); do
    case "$1" in
        --prefix) prefix="$2"; shift 2 ;;
        --project-root) project_root="$2"; shift 2 ;;
        --source) source_tree="$2"; shift 2 ;;
        --jobs) jobs="$2"; shift 2 ;;
        --repair) shift ;;
        *) echo "Unknown material-EOS installer argument: $1" >&2; exit 2 ;;
    esac
done
[[ -n "$prefix" && -n "$project_root" ]] || {
    echo "usage: $0 --prefix DIR --project-root DIR [--source DIR] [--jobs N]" >&2
    exit 2
}
project_root="$(cd "$project_root" && pwd)"
runtime_profile="material-eos"
source "$project_root/scripts/runtime_environment.sh"
arguments=(--prefix "$prefix" --project-root "$project_root" --jobs "$jobs")
[[ -z "$source_tree" ]] || arguments+=(--source "$source_tree")
"$python" "$project_root/skills/eos/scripts/bootstrap_material_eos.py" "${arguments[@]}"
