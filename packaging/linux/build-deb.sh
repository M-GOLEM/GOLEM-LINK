#!/usr/bin/env bash
# Build GOLEM-LINK-Debian13.deb on a Debian 13 machine.
#
# Requirements: python3 (3.11+), python3-venv, python3-pip, dpkg-deb >= 1.19,
# and Node.js 18+ for the dashboard build (skip with SKIP_WEB=1 when
# apps/dashboard/dist is already present).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/golem_link/version.py")"
[ -n "$VERSION" ] || { echo "could not read version" >&2; exit 1; }

BUILD="$ROOT/build/deb"
STAGE="$BUILD/root"
OUT="$ROOT/build/GOLEM-LINK-Debian13.deb"

for cmd in python3 dpkg-deb; do
    command -v "$cmd" >/dev/null || { echo "missing $cmd (install it first)" >&2; exit 1; }
done

if [ "${SKIP_WEB:-0}" != "1" ]; then
    if [ ! -f "$ROOT/apps/dashboard/dist/index.html" ]; then
        command -v npm >/dev/null || { echo "npm is required to build the dashboard (or set SKIP_WEB=1)" >&2; exit 1; }
        ( cd "$ROOT/apps/dashboard" && npm ci && npm run build )
    fi
else
    [ -f "$ROOT/apps/dashboard/dist/index.html" ] || { echo "SKIP_WEB=1 but apps/dashboard/dist is missing" >&2; exit 1; }
fi

rm -rf "$BUILD"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/opt/golem-link/wheels" \
         "$STAGE/usr/share/golem-link/web" \
         "$STAGE/usr/share/doc/golem-link" \
         "$STAGE/usr/bin" \
         "$STAGE/lib/systemd/system"

cp -a "$ROOT/apps/dashboard/dist/." "$STAGE/usr/share/golem-link/web/"
cp "$ROOT/packaging/linux/golem-link" "$STAGE/usr/bin/golem-link"
chmod 0755 "$STAGE/usr/bin/golem-link"
cp "$ROOT/packaging/linux/golem-link.service" "$STAGE/lib/systemd/system/"
cp "$ROOT/README.md" "$STAGE/usr/share/doc/golem-link/README.md"
cp "$ROOT/LICENSE" "$STAGE/usr/share/doc/golem-link/copyright" 2>/dev/null || true
cp -a "$ROOT/docs" "$STAGE/usr/share/doc/golem-link/docs"

echo "Collecting Python wheels (this needs network access once)…"
python3 -m pip wheel --no-cache-dir --wheel-dir "$STAGE/opt/golem-link/wheels" "$ROOT"

for script in postinst prerm postrm; do
    cp "$ROOT/packaging/linux/DEBIAN/$script" "$STAGE/DEBIAN/$script"
    chmod 0755 "$STAGE/DEBIAN/$script"
done
sed "s/@VERSION@/$VERSION/; s/@SIZE@/$(du -sk "$STAGE" | cut -f1)/" \
    "$ROOT/packaging/linux/DEBIAN/control" > "$STAGE/DEBIAN/control"
chmod 0644 "$STAGE/DEBIAN/control"

dpkg-deb --root-owner-group --build "$STAGE" "$OUT"
echo "Built $OUT"
echo "Verify with: dpkg-deb --info \"$OUT\" && dpkg-deb --contents \"$OUT\" | head -40"
echo "Install on Debian 13 with: sudo apt install \"$OUT\""
