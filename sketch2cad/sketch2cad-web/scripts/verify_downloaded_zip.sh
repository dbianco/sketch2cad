#!/usr/bin/env bash
# Run the offline package verifier (sketch2cad-api/verifier/sketch2cad_verify.py, task 001-092)
# on a downloaded final package, with the API project's locked Python environment.
#
#   bash scripts/verify_downloaded_zip.sh PACKAGE.zip [--key-id ed25519:...] [--public-key F]
#
# Every argument is passed to the verifier unchanged; its exit status is this script's: 0 when
# the package is intact and the signature valid, 1 when a check fails (the affected member is
# named in Spanish), 2 on usage errors. Requires `uv` on PATH (the first run syncs
# sketch2cad-api/.venv from uv.lock). Used by e2e/export_verify.spec.ts (jotdown H4-10).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
api_dir="$(cd "$script_dir/../../sketch2cad-api" && pwd)"

if [ "$#" -lt 1 ]; then
  echo "usage: $0 PACKAGE.zip [verifier options]" >&2
  exit 2
fi
command -v uv >/dev/null 2>&1 || { echo "error: uv is not installed" >&2; exit 2; }

# -I: isolated mode, so nothing next to the package or in the working directory is imported.
exec uv run --quiet --frozen --project "$api_dir" python -I "$api_dir/verifier/sketch2cad_verify.py" "$@"
