#!/bin/sh
# Creates (idempotently) the "droid" account: the user of Android's own Linux terminal VM, which
# apps written for that terminal (Antigravity) log in as and copy files to (/home/droid). Here it
# is a second name of one Android user's Terminal account: the same uid and gid, so Android sees
# its processes, files and network use as the Terminal's; its own home. Runs as root inside the
# environment (gbos-linuxd, after the owner approved a request from such an app).
#   $1 owner account   $2 uid (= gid) of the owner   $3 Android user id
set -eu
owner=$1
uid=$2
android_user=$3
droid=droid

fail() {
    echo "$1"
    exit 1
}

owner_line=$(getent passwd "$owner" || true)
[ -n "$owner_line" ] || fail "no Linux account $owner"
[ "$(echo "$owner_line" | cut -d: -f3)" = "$uid" ] || fail "$owner does not have uid $uid"

line=$(getent passwd "$droid" || true)
if [ -n "$line" ]; then
    current=$(echo "$line" | cut -d: -f3)
    [ "$current" = "$uid" ] || fail "the account $droid belongs to uid $current (another Android user)"
    [ "$(echo "$line" | cut -d: -f6)" = "/home/$droid" ] || fail "the home of $droid is not /home/$droid"
else
    # Appended after the owner: lookups by uid (ls, id, sudo, ps) keep showing the owner's name.
    useradd --non-unique --uid "$uid" --gid "$uid" --no-log-init --no-create-home \
        --home-dir "/home/$droid" --shell /bin/bash \
        --comment "Android Linux terminal user of $owner (Android user $android_user)" \
        -K UID_MAX=4294967293 -K GID_MAX=4294967293 "$droid"
fi
for group in sudo users aid_inet aid_net_raw aid_everybody; do
    getent group "$group" >/dev/null && usermod --append --groups "$group" "$droid"
done
# sudo finds the invoking user by uid (the owner, whose rule applies); this rule covers tools
# that pass the name.
sudoers=/etc/sudoers.d/googlebook-$droid
printf '%s ALL=(ALL:ALL) NOPASSWD: ALL\n' "$droid" > "$sudoers.tmp"
chmod 0440 "$sudoers.tmp"
if command -v visudo >/dev/null; then visudo -cqf "$sudoers.tmp"; fi
mv "$sudoers.tmp" "$sudoers"

# The VM's /mnt/shared is its owner's shared storage, with modes that let programs kept there
# run (gbos-linuxd's storage view, /mnt/storage/<user>, needs bindfs). A path the user made
# themselves is kept.
if ! command -v bindfs >/dev/null 2>&1; then
    set -- /var/cache/googlebook/debs/*.deb
    [ -e "$1" ] || fail "the image's bindfs package is missing (/var/cache/googlebook/debs)"
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y -q --no-install-recommends "$@" >/dev/null 2>&1 ||
        { apt-get update -q >/dev/null 2>&1 && apt-get install -y -q --no-install-recommends "$@" >/dev/null; } ||
        fail "bindfs could not be installed (network?)"
fi
if [ -L /mnt/shared ] || [ ! -e /mnt/shared ]; then
    ln -sfn "/mnt/storage/$android_user" /mnt/shared
fi
# Apps for Android's terminal start an SSH server for droid (Antigravity: port 2222 and up,
# its own key). Android's terminal keeps the VM's ports on the device; Linux here shares
# Android's network, so the server would also answer on Wi-Fi. Loopback only, unless the user
# removes this file (it is written once: a deleted file stays deleted).
loopback=/etc/ssh/sshd_config.d/00-googlebook-loopback.conf
written=/var/lib/googlebook/sshd-loopback
if [ ! -e "$written" ]; then
    mkdir -p /etc/ssh/sshd_config.d /var/lib/googlebook
    printf '%s\n' "# Googlebook Linux: SSH only from this device. Delete this file to listen on every network." \
        "ListenAddress 127.0.0.1" > "$loopback"
    : > "$written"
fi
echo "Linux account $droid ($uid) ready"
