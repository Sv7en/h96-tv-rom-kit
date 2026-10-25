#!/usr/bin/env bash
# surgery.sh — modify partitions inside the super blob of a Rockchip
# update.img, on a Linux CI runner (needs root/sudo for loop mounts).
#
# Inputs (relative to repo root):
#   work/original.img      stock update.img
#   overlay/               user surgery spec (see overlay/README.md)
# Outputs:
#   work/out/super_sparse.img     modified super (sparse)
#   work/out/vbmeta.img           patched vbmeta (flags=3)
set -euo pipefail
cd "$(dirname "$0")/.."

PY=python3
TOOLS=tools
IN=work/original.img
OUT=work/out
OVERLAY=${1:-overlay}

echo '== [0/7] unpacking update.img'
$PY $TOOLS/rkfw.py unpack "$IN" $OUT/unpacked

echo '== [1/7] unsparsing super'
$PY $TOOLS/sparseimg.py unsparse $OUT/unpacked/super.img $OUT/super_raw.img
rm -f $OUT/unpacked/super.img   # free ~2.3GB on CI runners

echo '== [2/7] mapping partitions in super'
$PY $TOOLS/lptools.py $OUT/super_raw.img --json > $OUT/partitions.json
cat $OUT/partitions.json
SYSTEM_OFF=$($PY -c "import json;print(json.load(open('$OUT/partitions.json'))['system']['offset'])")
SYSTEM_SIZE=$($PY -c "import json;print(json.load(open('$OUT/partitions.json'))['system']['size'])")
echo "system @ $SYSTEM_OFF size $SYSTEM_SIZE"

echo '== [3/7] carving system ext4'
dd if=$OUT/super_raw.img of=$OUT/system.img bs=1M iflag=count_bytes status=none \
   skip=$SYSTEM_OFF count=$SYSTEM_SIZE
e2fsck -fy $OUT/system.img || true

echo '== [4/7] applying overlay'
MNT=$OUT/system_mnt
mkdir -p "$MNT"
sudo mount -o loop $OUT/system.img "$MNT"
trap 'sudo umount "$MNT" || true' EXIT

# remember the SELinux label used for /system/app contents
APP_CTX=$(ls -Zd "$MNT/system/app" 2>/dev/null | awk '{print $1}')
echo "system/app label: $APP_CTX"

# --- 4a. removals -------------------------------------------------------
if [ -f "$OVERLAY/remove.list" ]; then
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue;; esac
    target="$MNT/$line"
    if [ -e "$target" ]; then
      echo "rm -rf /system/$line"
      sudo rm -rf "$target"
    else
      echo "WARNING: remove.list entry not found: $line" >&2
    fi
  done < "$OVERLAY/remove.list"
fi

# --- 4b. file overlay ----------------------------------------------------
if [ -d "$OVERLAY/system" ]; then
  (cd "$OVERLAY/system" && find . -type f | while read -r f; do
    dest="$MNT/${f#./}"
    echo "copy ${f#./} -> /system/${f#./}"
    sudo mkdir -p "$(dirname "$dest")"
    sudo cp "$OVERLAY/system/$f" "$dest"
    sudo chmod 0644 "$dest"
    sudo chown 0:0 "$dest"
    # match SELinux label of an existing sibling file, fall back to app label
    sib_ctx=$(ls -Z "$(dirname "$dest")" 2>/dev/null | head -2 | tail -1 | awk '{print $1}')
    ctx="${sib_ctx:-$APP_CTX}"
    [ -n "$ctx" ] && sudo chcon "$ctx" "$dest" || true
  done)
fi

# --- 4c. prop overrides ---------------------------------------------------
# every overlay/props/*.prop is merged into /system/build.prop:
# existing keys are replaced, new keys appended.
if ls "$OVERLAY"/props/*.prop >/dev/null 2>&1; then
  dest="$MNT/system/build.prop"
  for prop in "$OVERLAY"/props/*.prop; do
    echo "merging $(basename "$prop") into /system/build.prop"
    while IFS= read -r line; do
      case "$line" in ''|\#*) continue;; esac
      key="${line%%=*}"
      if sudo grep -q "^${key}=" "$dest" 2>/dev/null; then
        sudo sed -i "s|^${key}=.*|${line}|" "$dest"
      else
        echo "$line" | sudo tee -a "$dest" >/dev/null
      fi
    done < "$prop"
  done
fi

# --- 4d. preinstalled apps ----------------------------------------------
# three sources, each holding subdirs of the form <AppName>/<file>.apk:
#   overlay/apks              bundled in-repo (dangbei etc.; CN CDNs block CI)
#   work/apps/system-app      downloaded by the workflow -> /system/app
#   work/apps/system-priv-app downloaded by the workflow -> /system/priv-app
install_apks() {
  local src_rel="$1"     # e.g. app or priv-app
  local src_dir="$2"
  [ -d "$src_dir" ] || return 0
  # inherit SELinux label from an existing sibling app folder
  local ctx
  ctx=$(ls -Zd "$MNT/$src_rel"/* 2>/dev/null | head -1 | awk '{print $1}')
  [ -n "$ctx" ] || ctx="$APP_CTX"
  for d in "$src_dir"/*/; do
    [ -f "$d"/*.apk ] 2>/dev/null || continue
    local name; name=$(basename "$d")
    echo "install $src_rel/$name"
    sudo rm -rf "$MNT/$src_rel/$name"
    sudo cp -r "$d" "$MNT/$src_rel/$name"
    sudo find "$MNT/$src_rel/$name" -type f -exec chmod 0644 {} +
    sudo find "$MNT/$src_rel/$name" -type d -exec chmod 0755 {} +
    sudo chown -R 0:0 "$MNT/$src_rel/$name"
    if [ -n "$ctx" ]; then
      sudo chcon -R "$ctx" "$MNT/$src_rel/$name" 2>/dev/null || true
    fi
  done
}
# safety: removing the stock launcher without a replacement home app
# would leave the box with no launcher at all — refuse to build
STAGED=$(find "$OVERLAY/apks" work/apps -name '*.apk' 2>/dev/null | wc -l)
if grep -q '^system/app/MsLauncher_v1' "$OVERLAY/remove.list" 2>/dev/null \
   && [ "$STAGED" -eq 0 ]; then
  echo 'FATAL: remove.list 删除原厂桌面 MsLauncher_v1,但 overlay/apks/ 与 apps.txt 都没有提供替代桌面 APK(当贝桌面)。无桌面 = 开机黑屏,已中止。' >&2
  exit 1
fi

install_apks system/app       "$OVERLAY/apks"
install_apks system/app       work/apps/system-app
install_apks system/priv-app work/apps/system-priv-app

df -h "$MNT" || true

echo '== [5/7] unmounting and checking'
sync
sudo umount "$MNT"
trap - EXIT
e2fsck -fy $OUT/system.img

echo '== [6/7] writing system back into super'
dd if=$OUT/system.img of=$OUT/super_raw.img bs=1M oflag=seek_bytes seek=$SYSTEM_OFF \
   conv=notrunc status=none
# sanity: filesystem size must not have changed
NEW_SIZE=$(stat -c%s $OUT/system.img)
[ "$NEW_SIZE" = "$SYSTEM_SIZE" ] || { echo "FATAL: system size changed ($NEW_SIZE != $SYSTEM_SIZE)"; exit 1; }

echo '== [7/7] re-sparsing super and patching vbmeta'
$PY $TOOLS/sparseimg.py sparse $OUT/super_raw.img $OUT/super_sparse.img
$PY $TOOLS/vbmeta_patch.py $OUT/unpacked/vbmeta.img $OUT/vbmeta.img
rm -f $OUT/super_raw.img $OUT/system.img $OUT/super_raw2.img 2>/dev/null || true

echo 'surgery complete:'
ls -la $OUT/super_sparse.img $OUT/vbmeta.img
