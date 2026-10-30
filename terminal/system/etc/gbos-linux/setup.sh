#!/bin/sh
# Turns Debian's container image into the Googlebook Linux environment. gbos-linuxd runs this as
# root inside the environment after unpacking, and again on repair: every step is idempotent.
#   $1 packages (space separated)   $2 locales, first one is the default   $3 1 if _apt needs aid_inet
# Progress for the installer: "gbos:step:<from%>:<to%>:<label>" on fd 3, then apt's Status-Fd lines.
set -eu
packages=$1
locales=$2
apt_needs_inet=$3

step() { echo "gbos:step:$1:$2:$3" >&3; }
apt_get() {
    apt-get -o APT::Status-Fd=3 -o Dpkg::Use-Pty=0 \
        -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"
}
export DEBIAN_FRONTEND=noninteractive

step 0 2 prepare
# The image is tuned for throw-away containers. Here it is a long-lived system on a battery
# device: no unsafe dpkg I/O, normal apt caches, translated descriptions, standard autoremove.
rm -f /etc/dpkg/dpkg.cfg.d/docker-apt-speedup \
      /etc/apt/apt.conf.d/docker-clean \
      /etc/apt/apt.conf.d/docker-gzip-indexes \
      /etc/apt/apt.conf.d/docker-no-languages \
      /etc/apt/apt.conf.d/docker-autoremove-suggests
# There is no init system: postinst scripts must not try to start services
# (policy-rc.d from the image says so; keep it).
[ -x /usr/sbin/policy-rc.d ] || { printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d; chmod 755 /usr/sbin/policy-rc.d; }
# A previous run may have been interrupted in the middle of dpkg.
dpkg --configure -a
# Android's groups the Terminal accounts carry: sockets on kernels with paranoid networking,
# raw sockets for ping, and everybody for the user's shared storage (/mnt/android/<user>).
getent group aid_inet >/dev/null || groupadd --gid 3003 aid_inet
getent group aid_net_raw >/dev/null || groupadd --gid 3004 aid_net_raw
getent group aid_everybody >/dev/null || groupadd --gid 9997 aid_everybody
# New accounts (useradd/adduser) must not get Android's uids: 1000-9999 are Android system ids
# (1000 = system), 10000-59999 app/cache/shared ids. 60000-89999 is unused in every Android user.
sed -i -E 's/^#?(UID_MIN|GID_MIN)\s.*/\1 60000/; s/^#?(UID_MAX|GID_MAX)\s.*/\1 89999/' /etc/login.defs
if [ -f /etc/adduser.conf ]; then
    sed -i -E 's/^#?(FIRST_UID|FIRST_GID)=.*/\1=60000/; s/^#?(LAST_UID|LAST_GID)=.*/\1=89999/' /etc/adduser.conf
fi
if [ "$apt_needs_inet" = 1 ]; then
    # apt drops to _apt with only its primary group; on such kernels that group must be inet.
    usermod --gid aid_inet _apt
fi

step 2 14 update
apt_get update

step 14 38 upgrade
apt_get -y full-upgrade

step 38 92 install
# shellcheck disable=SC2086
apt_get -y install --no-install-recommends $packages
# Packages the image carries (gbos-linuxd copies them to /var/cache/googlebook/debs).
set -- /var/cache/googlebook/debs/*.deb
[ -e "$1" ] && apt_get -y install --no-install-recommends "$@"

step 92 98 locales
mkdir -p /var/lib/googlebook
generated="C.UTF-8"
for l in $locales; do
    [ "$l" = C.UTF-8 ] && continue
    charset=${l#*.}
    [ "$charset" = "$l" ] && charset=ISO-8859-1
    if grep -q "^$l $charset\$" /usr/share/i18n/SUPPORTED; then
        if ! grep -q "^$l $charset\$" /etc/locale.gen; then
            if grep -q "^# *$l $charset\$" /etc/locale.gen; then
                sed -i "s/^# *$l $charset\$/$l $charset/" /etc/locale.gen
            else
                echo "$l $charset" >> /etc/locale.gen
            fi
        fi
        generated="$generated $l"
    else
        echo "locale $l is not supported by glibc, skipped"
    fi
done
locale-gen
printf '%s\n' $generated > /var/lib/googlebook/locales
default=C.UTF-8
for l in $locales; do
    if grep -qx "$l" /var/lib/googlebook/locales; then default=$l; break; fi
done
update-locale "LANG=$default"

step 98 100 finish
apt_get clean
echo "Googlebook Linux: $(cat /etc/debian_version), $(dpkg-query -W -f='${binary:Package}\n' | wc -l) packages"
