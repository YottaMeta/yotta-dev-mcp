#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$(uname -s)" in
  MINGW*|MSYS*) SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -W)" ;;
esac

exec node "$SOURCE_DIR/bin/install.js" "$@"
