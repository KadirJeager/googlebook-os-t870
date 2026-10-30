#!/bin/sh
# Generates one more locale (Android's language changed to one not generated yet).
#   $1 locale, e.g. de_DE.UTF-8
set -eu
l=$1
charset=${l#*.}
[ "$charset" = "$l" ] && charset=ISO-8859-1
grep -q "^$l $charset\$" /usr/share/i18n/SUPPORTED || { echo "unsupported locale $l"; exit 1; }
if ! grep -q "^$l $charset\$" /etc/locale.gen; then
    if grep -q "^# *$l $charset\$" /etc/locale.gen; then
        sed -i "s/^# *$l $charset\$/$l $charset/" /etc/locale.gen
    else
        echo "$l $charset" >> /etc/locale.gen
    fi
fi
locale-gen
mkdir -p /var/lib/googlebook
grep -qx "$l" /var/lib/googlebook/locales 2>/dev/null || echo "$l" >> /var/lib/googlebook/locales
