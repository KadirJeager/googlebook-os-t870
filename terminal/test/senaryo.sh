# Runs on the test emulator as root (test/emulator.sh senaryo): the whole Linux environment, as the
# updater and the Terminal use it. Every step prints NAME=<exit status>; all 0 is a pass.
getenforce
gbos-linux remove --yes; echo remove=$?
gbos-linux install --locale tr_TR.UTF-8 --locale en_US.UTF-8 > /data/local/tmp/gbos-install.log 2>&1; echo install=$?
tail -1 /data/local/tmp/gbos-install.log
gbos-linux useradd --android-user 0 jk; echo useradd=$?
gbos-linux run -- sh -c "uname -m; cat /etc/debian_version; grep CapBnd /proc/self/status; getent hosts deb.debian.org | head -1"; echo run=$?
gbos-linux run -- sh -c "exit 7"; [ $? = 7 ]; echo exitcode=$?
gbos-linux run -- yok-boyle-komut 2>/dev/null; [ $? = 127 ]; echo notfound=$?
# The Linux user's processes run with the Terminal's uid: Android gives that uid network only
# while the app is running (foreground service), so the user half runs with the Terminal open.
am start -W -n com.gbos.terminal/.TerminalActivity >/dev/null; sleep 4
gbos-linux run --user jk -- sh -c 'id -un; sudo -n true && echo SUDO_OK; echo e > ~/android/Download/e.txt && echo STORAGE_OK; ping -c1 -W3 1.1.1.1 | tail -1; timeout 20 python3 -c "import urllib.request as u; print(u.urlopen(\"https://deb.debian.org\").status)"; timeout 30 git ls-remote https://github.com/termux/termux-app HEAD | cut -c1-12; sudo apt-get install -y sl >/dev/null 2>&1 && echo APT_OK'; echo user=$?
ls /sdcard/Download/e.txt >/dev/null; echo storage_android=$?
mkdir -p /data/gbos-linux/test-bind
gbos-linux run --bind /data/gbos-linux/test-bind:/work -- touch /work/z; ls /data/gbos-linux/test-bind/z >/dev/null; echo bind=$?
rm -rf /data/gbos-linux/test-bind
gbos-linux run --user jk -- xdg-open https://www.debian.org; echo xdgopen=$?
input keyevent KEYCODE_HOME; sleep 2
gbos-linux run -- sh -c "useradd -m deneme && [ \$(id -u deneme) -ge 60000 ] && userdel -r deneme"; echo uidrange=$?
gbos-linux stop; echo stop=$?
gbos-linux run -- true; echo restart=$?
