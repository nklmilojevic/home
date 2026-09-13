#!/usr/bin/env bash
# Read-only, isolated-process tests against the dependencies in the running image.
# Pass --live to additionally make three tiny synthetic ChatGPT requests.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
python3 - <<'PY' | kubectl -n ai exec -i deployment/litellm -- python - "$@"
from pathlib import Path
source = Path('../app/patches/chatgpt-transformation.py.txt').read_text()
print('PATCH_SOURCE = ' + repr(source))
print(Path('chatgpt_patch_test.py.txt').read_text())
PY
