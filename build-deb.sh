#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
VERSION=$(grep '^Version:' packaging/DEBIAN/control | awk '{print $2}')
ARCH=$(grep '^Architecture:' packaging/DEBIAN/control | awk '{print $2}')
STAGE=build/pkg
RNNOISE_URL="https://github.com/werman/noise-suppression-for-voice/releases/download/v1.10/linux-rnnoise.zip"
VENDOR="vendor/librnnoise_ladspa.so"

rm -rf build && mkdir -p "$STAGE" dist vendor

if [ ! -f "$VENDOR" ]; then
  tmp=$(mktemp -d)
  curl -L --fail --retry 3 -o "$tmp/linux-rnnoise.zip" "$RNNOISE_URL"
  unzip -j -o "$tmp/linux-rnnoise.zip" "linux-rnnoise/ladspa/librnnoise_ladspa.so" -d vendor
  rm -rf "$tmp"
fi

cp -r packaging/. "$STAGE/"
mkdir -p "$STAGE/usr/share/beats/ladspa"
mkdir -p "$STAGE/usr/share/pixmaps"
cp src/headset_mic_fixer.py src/beats_logo.py src/beats_theme.py "$STAGE/usr/share/beats/"
mkdir -p "$STAGE/usr/share/doc/beats"
cp LICENSE "$STAGE/usr/share/doc/beats/"
cp "$VENDOR" "$STAGE/usr/share/beats/ladspa/"
cp packaging/usr/share/icons/hicolor/scalable/apps/beats.svg \
   "$STAGE/usr/share/pixmaps/beats.svg"
ln -sf beats "$STAGE/usr/bin/headset-mic-fixer"

/usr/bin/python3 - <<PY
import os
import shutil
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

svg_path = "packaging/usr/share/icons/hicolor/scalable/apps/beats.svg"
svg = open(svg_path, "rb").read()
stage = "${STAGE}"
for size in (16, 24, 32, 48, 64, 128, 256):
    d = os.path.join(stage, "usr/share/icons/hicolor", f"{size}x{size}", "apps")
    os.makedirs(d, exist_ok=True)
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(size, size)
    loader.write(svg)
    loader.close()
    loader.get_pixbuf().savev(os.path.join(d, "beats.png"), "png", [], [])
png128 = os.path.join(stage, "usr/share/icons/hicolor/128x128/apps/beats.png")
os.makedirs(os.path.join(stage, "usr/share/pixmaps"), exist_ok=True)
if os.path.isfile(png128):
    shutil.copy2(png128, os.path.join(stage, "usr/share/pixmaps/beats.png"))
PY

chmod 755 "$STAGE/usr/bin/beats"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"
/usr/bin/python3 -m py_compile src/headset_mic_fixer.py
find src -name __pycache__ -prune -exec rm -rf {} +
dpkg-deb --root-owner-group --build "$STAGE" "dist/beats_${VERSION}_${ARCH}.deb"
echo "Built dist/beats_${VERSION}_${ARCH}.deb"
