#!/usr/bin/env bash
# A clean emulator phone, paired with a test brain on this computer, hands on.
# Never the person's phone: Android's security settings are flipped here by
# adb, which is fine on an emulator and only there.
#
#   apps/mobile/e2e/emulator.sh <test brain home>     (the brain's HOME, as for heare-brain)
#   then: HEARE_E2E_HOME=<home> ANDROID_SERIAL=emulator-5554 python3 apps/mobile/e2e/hands.py
set -euo pipefail

HOME_DIR=${1:?give the test brain home}
export ANDROID_SERIAL=${ANDROID_SERIAL:-emulator-5554}
[[ $ANDROID_SERIAL == emulator-* ]] || { echo "only an emulator: $ANDROID_SERIAL"; exit 1; }
APP=dev.onelenyk.heare
HERE=$(cd "$(dirname "$0")/.." && pwd)
PORT=$(sed -n 's/^api_port *= *\([0-9]*\).*/\1/p' "$HOME_DIR/.heare/settings/config.toml")
TOKEN=$(cat "$HOME_DIR/.heare/machine/api_token")
BRAIN="http://127.0.0.1:${PORT:-9790}"

adb wait-for-device
until [[ $(adb shell getprop sys.boot_completed | tr -d '\r') == 1 ]]; do sleep 2; done

adb uninstall "$APP" >/dev/null 2>&1 || true
adb install "$HERE/phone/build/outputs/apk/debug/phone-debug.apk" >/dev/null
adb shell pm grant "$APP" android.permission.RECORD_AUDIO
adb shell pm grant "$APP" android.permission.POST_NOTIFICATIONS
# A sideloaded app's accessibility is a «restricted setting» since Android 13.
adb shell appops set "$APP" ACCESS_RESTRICTED_SETTINGS allow

code=$(curl -sf -X POST -H "X-Heare-Token: $TOKEN" "$BRAIN/api/pair-code" | python3 -c 'import json,sys; print(json.load(sys.stdin)["code"])')
machine=$(curl -sf -X POST -H 'Content-Type: application/json' \
  -d "{\"kind\":\"machine\",\"code\":\"$code\",\"name\":\"emu-e2e\"}" "$BRAIN/api/pair" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
prefs=$(mktemp)
cat > "$prefs" <<EOF
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="brain">http://10.0.2.2:${PORT:-9790}</string>
    <string name="machine_token">$machine</string>
    <string name="name">emu-e2e</string>
    <boolean name="hands_free" value="false" />
</map>
EOF
adb push "$prefs" /data/local/tmp/heare.xml >/dev/null
rm -f "$prefs"
adb shell run-as "$APP" sh -c "'mkdir -p shared_prefs && cp /data/local/tmp/heare.xml shared_prefs/heare.xml'"
adb shell rm -f /data/local/tmp/heare.xml

adb shell am start -n "$APP/$APP.phone.MainActivity" >/dev/null
# Right after an install Android may clear the setting once more: put it until
# the service is bound, and say so if it never is.
for _ in $(seq 1 15); do
  adb shell settings put secure enabled_accessibility_services "$APP/$APP.phone.Hands"
  adb shell settings put secure accessibility_enabled 1
  sleep 2
  adb shell dumpsys accessibility | grep -q 'label=heare' && break
done
adb shell dumpsys accessibility | grep -q 'label=heare' || { echo "the hands service never bound"; exit 1; }
echo "emulator ready: emu-e2e on $BRAIN"
