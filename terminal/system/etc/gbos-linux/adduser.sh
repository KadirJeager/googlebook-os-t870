#!/bin/sh
# Creates the Linux account of an Android user's Terminal (idempotent). Runs as root inside the
# environment. The account uses the Terminal app's Android uid, so files in ~/android (Android's
# shared storage) belong to the Terminal and Android's own permission checks apply to it.
#   $1 name   $2 uid (= gid)   $3 Android user id
set -eu
name=$1
uid=$2
android_user=$3

existing=$(getent passwd "$uid" | cut -d: -f1 || true)
if [ -n "$existing" ] && [ "$existing" != "$name" ]; then
    echo "uid $uid already belongs to $existing"
    exit 1
fi
if [ -z "$existing" ]; then
    if getent passwd "$name" >/dev/null; then
        echo "the name $name is already taken"
        exit 1
    fi
    getent group "$uid" >/dev/null || groupadd --gid "$uid" "$name"
    # --no-log-init: no lastlog/faillog records at these large uids (sparse but pointless).
    useradd --uid "$uid" --gid "$uid" --no-log-init --no-create-home \
        --home-dir "/home/$name" --shell /bin/bash --comment "Android user $android_user" \
        -K UID_MAX=4294967293 -K GID_MAX=4294967293 "$name"
fi
getent group aid_everybody >/dev/null || groupadd --gid 9997 aid_everybody
for group in sudo users aid_inet aid_net_raw aid_everybody; do
    getent group "$group" >/dev/null && usermod --append --groups "$group" "$name"
done
# No password exists to type: the owner of the device is already authenticated by Android.
sudoers=/etc/sudoers.d/googlebook-$name
printf '%s ALL=(ALL:ALL) NOPASSWD: ALL\n' "$name" > "$sudoers.tmp"
chmod 0440 "$sudoers.tmp"
if command -v visudo >/dev/null; then visudo -cqf "$sudoers.tmp"; fi
mv "$sudoers.tmp" "$sudoers"
echo "Linux account $name ($uid) ready"
