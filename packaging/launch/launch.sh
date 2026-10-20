#!/usr/bin/env bash
set -euo pipefail
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$package_root/.venv/bin/simjecture" ]]; then
  echo 'Run ./setup.sh first (see INSTALL.md).' >&2
  exit 1
fi
export PATH="$package_root/.venv/bin:$PATH"
mkdir -p "$package_root/campaigns"
cd "$package_root/campaigns"
exec "$package_root/.venv/bin/simjecture" web "$@"
