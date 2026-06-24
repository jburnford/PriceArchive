#!/bin/bash
# Pull the canonical OCR result.json files from Nibi into data/ocr/.
# (Only result.json, not the .bad/.drift repair backups.)
set -euo pipefail
cd "$(dirname "$0")"
rsync -a --include='*/' --include='result.json' --exclude='*' \
  nibi:/project/6080182/infinity/output/price/ data/ocr/
echo "synced: $(find data/ocr -name result.json | wc -l) result.json files"
