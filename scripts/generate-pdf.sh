#!/usr/bin/env bash
set -euo pipefail

input="${1:-docs/proyecto.md}"
output="${2:-docs/proyecto.pdf}"

if command -v xelatex >/dev/null 2>&1; then
  pandoc "$input" -o "$output" --pdf-engine=xelatex
  exit 0
fi

if command -v pdflatex >/dev/null 2>&1; then
  pandoc "$input" -o "$output" --pdf-engine=pdflatex
  exit 0
fi

if python3 -c 'import reportlab' >/dev/null 2>&1; then
  python3 scripts/markdown_to_pdf.py "$input" "$output"
  exit 0
fi

if command -v cupsfilter >/dev/null 2>&1; then
  tmp_html="$(mktemp -t podman-watchguard-doc.XXXXXX.html)"
  pandoc "$input" -s -o "$tmp_html"
  if cupsfilter "$tmp_html" > "$output"; then
    rm -f "$tmp_html"
    exit 0
  fi
  rm -f "$tmp_html" "$output"
fi

echo "No PDF engine found. Install MacTeX, BasicTeX, wkhtmltopdf, or Python reportlab." >&2
exit 1
