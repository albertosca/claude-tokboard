#!/usr/bin/env bash
# Builds the bilingual docs site into site/ - one Zensical build per language,
# assembled afterwards (building both into a shared site_dir makes each build
# wipe the other language).
#
# The pages in guide/ reference their screenshots as images/<name>; the files
# live once, in assets/, and are copied into a throwaway source tree per
# language so the README and the site share a single copy.
#
# Usage: bash scripts/build-docs.sh
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf .docs-src .docs-build site
for lang in en pt; do
  mkdir -p ".docs-src/$lang/images"
  cp -R "guide/$lang/." ".docs-src/$lang/"
  cp "assets/dashboard-$lang.png" ".docs-src/$lang/images/dashboard.png"
  cp "assets/tui-$lang.gif" ".docs-src/$lang/images/tui.gif"
done

uv run --no-project --with zensical==0.0.73 zensical build --strict -f zensical.en.yml
uv run --no-project --with zensical==0.0.73 zensical build --strict -f zensical.pt.yml

mkdir -p site
cp -R .docs-build/en/. site/
cp -R .docs-build/pt site/pt
