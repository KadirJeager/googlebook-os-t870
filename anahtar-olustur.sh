#!/usr/bin/env bash
# Creates the project's private signing keys once; never overwrites one that exists.
#
#   ./anahtar-olustur.sh
#
# Every key lives outside the project in $GBOS_ANAHTAR (default ~/.android-certs/googlebookos-t870,
# mode 0700, files 0600). Whoever builds an image signs it with their own keys; none is published.
#
#   ota.key.pem / ota.pk8 /  RSA-4096 key that signs the recovery install package (kurulum.zip,
#   ota.x509.pem             guncelleyici/kurtarma/paket.py). recovery-anahtar-ekle.py adds the
#                            certificate to the recovery's otacerts.zip; recovery refuses packages
#                            signed with anything else.
#   uygulama.p12             apps apply_patches.py changes, the "Sistem güncellemesi" app and the
#                            tablet mode tile (password gbos-uygulama). The tablet's kit carries it.
#   terminal.keystore +      the Googlebook Terminal and its compatibility package
#   terminal.pass            (terminal/build.sh); the Terminal trusts only a package signed like
#                            itself. The password is random and stays in terminal.pass.
#   sense.keystore           the face unlock app (yuz-tanima/build.sh, password android).
#
# A key must stay the same across a device's updates: Android refuses an app update signed with
# another key, so keep the directory backed up.
set -euo pipefail

DIR="${GBOS_ANAHTAR:-$HOME/.android-certs/googlebookos-t870}"
ORG="Googlebook OS T870"

install -d -m 700 "$DIR"
umask 077

hazir() { [[ -e "$DIR/$1" ]] && echo "zaten var: $DIR/$1"; }

# genkey FILE ALIAS STOREPASS BITS DNAME
genkey() {
    keytool -genkeypair -keystore "$DIR/$1" -storetype PKCS12 -alias "$2" -keyalg RSA -keysize "$4" \
        -validity 10950 -storepass "$3" -keypass "$3" -dname "$5" >/dev/null
    echo "oluşturuldu: $DIR/$1"
}

hazir ota.pk8 || {
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -pkeyopt rsa_keygen_pubexp:65537 \
        -out "$DIR/ota.key.pem" 2>/dev/null
    openssl req -new -x509 -sha256 -days 10950 -key "$DIR/ota.key.pem" \
        -subj "/C=TR/O=$ORG/CN=$ORG guncelleyici" -out "$DIR/ota.x509.pem"
    openssl pkcs8 -topk8 -nocrypt -inform PEM -outform DER -in "$DIR/ota.key.pem" -out "$DIR/ota.pk8"
    echo "oluşturuldu: $DIR/ota.pk8"
}
hazir uygulama.p12 || genkey uygulama.p12 uygulama gbos-uygulama 4096 "CN=$ORG Sistem guncellemesi, O=$ORG, C=TR"
hazir terminal.keystore || {
    printf "%s" "$(openssl rand -hex 16)" > "$DIR/terminal.pass"
    genkey terminal.keystore terminal "$(cat "$DIR/terminal.pass")" 4096 "CN=Googlebook Terminal, O=$ORG"
}
hazir sense.keystore || genkey sense.keystore sense android 4096 "CN=SenseFaceUnlock, O=$ORG, C=TR"

chmod 600 "$DIR"/*
openssl x509 -in "$DIR/ota.x509.pem" -noout -subject -fingerprint -sha256
