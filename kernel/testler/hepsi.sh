#!/usr/bin/env bash
# Bütün kernel testlerini sırayla koşar; biri bile kalırsa çıkış kodu 1.
D="$(cd "$(dirname "$0")" && pwd)"
durum=0
for t in config-denk surum-dizesi modul-crc yama-uygulandi boot-alanlari; do
	"$D/$t.sh" || durum=1
done
[ $durum = 0 ] && echo "TÜM KERNEL TESTLERİ GEÇTİ" || echo "KALAN TEST VAR"
exit $durum
