#!/bin/sh
python - <<'PY'
from app.services import slugify
assert slugify('İstanbul’da yeni gelişme!').startswith('stanbul') or slugify('İstanbul’da yeni gelişme!').startswith('istanbul')
print('basic tests ok')
PY
