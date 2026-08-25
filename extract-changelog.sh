#!/usr/bin/env bash
# extract-changelog.sh <version>   e.g. ./extract-changelog.sh 0.6.0
# Prints the CHANGELOG.md body for one version, for use as GitHub Release notes.
# Emits nothing if the heading format is not `## [<version>] — <date>`.
set -euo pipefail
awk -v v="$1" '
  $0 ~ "^## \\[" v "\\]" { on=1; next }
  on && /^## \[/         { exit }
  on                     { print }
' CHANGELOG.md
