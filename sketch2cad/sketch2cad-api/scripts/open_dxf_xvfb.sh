#!/usr/bin/env bash
# Open a DXF in LibreCAD under a virtual X display and fail if the load fails (H4-2).
#
# Usage (from sketch2cad-api/):
#   bash scripts/open_dxf_xvfb.sh tests/golden/plan.dxf
#
# LibreCAD (2.2 or later) converts the file with its `dxf2pdf` command inside `xvfb-run`;
# the check passes when LibreCAD exits 0 within DXF_VIEWER_TIMEOUT seconds (default 60) and
# writes a non-empty PDF. That proves LibreCAD parses and draws the file without crashing;
# it does not compare what is drawn (see docs/dxf-viewers.md).
#
# Exit status:
#   0  LibreCAD loaded the file, or a tool is missing (prints "SKIP: ...") and
#      DXF_VIEWER_REQUIRED is not 1
#   1  LibreCAD failed, crashed, timed out or wrote no PDF
#   2  usage error or the DXF does not exist
#   3  a tool is missing and DXF_VIEWER_REQUIRED=1 (the CI job sets it)
set -euo pipefail

TIMEOUT_SECONDS="${DXF_VIEWER_TIMEOUT:-60}"
REQUIRED="${DXF_VIEWER_REQUIRED:-0}"

if [[ $# -ne 1 ]]; then
  echo "usage: $0 FILE.dxf" >&2
  exit 2
fi
dxf="$1"
if [[ ! -f "$dxf" ]]; then
  echo "ERROR: no such file: $dxf" >&2
  exit 2
fi

missing=()
for tool in librecad xvfb-run timeout; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if [[ ${#missing[@]} -gt 0 ]]; then
  if [[ "$REQUIRED" == "1" ]]; then
    echo "ERROR: missing ${missing[*]}; install librecad, xvfb and coreutils" >&2
    exit 3
  fi
  echo "SKIP: missing ${missing[*]}; the DXF was not opened (see docs/dxf-viewers.md)"
  exit 0
fi

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
cp "$dxf" "$workdir/plan.dxf"
pdf="$workdir/plan.pdf"

status=0
timeout --kill-after=10 "$TIMEOUT_SECONDS" \
  xvfb-run --auto-servernum librecad dxf2pdf -o "$pdf" "$workdir/plan.dxf" \
  >"$workdir/librecad.log" 2>&1 || status=$?

if [[ $status -ne 0 ]]; then
  if [[ $status -eq 124 || $status -eq 137 ]]; then
    echo "FAIL: LibreCAD did not finish within ${TIMEOUT_SECONDS}s" >&2
  else
    echo "FAIL: LibreCAD exited with status $status" >&2
  fi
  cat "$workdir/librecad.log" >&2
  exit 1
fi
if [[ ! -s "$pdf" ]]; then
  echo "FAIL: LibreCAD exited 0 but wrote no PDF" >&2
  cat "$workdir/librecad.log" >&2
  exit 1
fi
echo "OK: LibreCAD opened $dxf ($(wc -c <"$pdf" | tr -d ' ') bytes of PDF)"
