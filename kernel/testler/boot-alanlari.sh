#!/usr/bin/env bash
# Yeni boot imajları, LineageOS zip'indeki boot.img ile kernel dışında her alanda
# aynı olmalı: başlık sürümü, adresler, cmdline, os sürümü/yaması, ramdisk, dtb, bölüm
# boyu ve AVB hash footer'ı (algoritma, salt, özellikler). Kernel = derlenen Image.
. "$(dirname "$0")/ortak.sh"
echo "boot-alanlari"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
lineage_boot_gerekli
boot_ac "$LINEAGE_BOOT" "$T/lineage"
unpack_bootimg --boot_img "$LINEAGE_BOOT" --out "$T/lineage" | grep -v '^kernel_size:' > "$T/lineage.bilgi"
avbtool info_image --image "$LINEAGE_BOOT" | grep -vE 'Original image size|VBMeta offset|Image Size|Digest' > "$T/lineage.avb"
for t in yamasiz yamali; do
	B="$CIKTI/$t/boot.img"
	gerekli "$B" "boot $t"
	unpack_bootimg --boot_img "$B" --out "$T/$t" | grep -v '^kernel_size:' > "$T/$t.bilgi"
	cmp -s "$T/lineage.bilgi" "$T/$t.bilgi" \
		&& gecti "$t: başlık alanları (sürüm 2, adresler, cmdline, os sürümü/yaması) LineageOS'unkiyle aynı" \
		|| { kaldi "$t: başlık alanları farklı"; diff "$T/lineage.bilgi" "$T/$t.bilgi"; }
	cmp -s "$T/lineage/ramdisk" "$T/$t/ramdisk" && cmp -s "$T/lineage/dtb" "$T/$t/dtb" \
		&& gecti "$t: ramdisk ve dtb LineageOS'unkiyle bayt bayt aynı" || kaldi "$t: ramdisk/dtb farklı"
	cmp -s "$T/$t/kernel" "$CIKTI/$t/Image" \
		&& gecti "$t: kernel = cikti/$t/Image" || kaldi "$t: kernel Image ile aynı değil"
	[ "$(stat -c%s "$B")" = "$BOOT_BOLUM_BOYUTU" ] \
		&& gecti "$t: imaj boyu = boot bölümü ($BOOT_BOLUM_BOYUTU bayt)" || kaldi "$t: imaj boyu farklı"
	avbtool info_image --image "$B" | grep -vE 'Original image size|VBMeta offset|Image Size|Digest' > "$T/$t.avb"
	cmp -s "$T/lineage.avb" "$T/$t.avb" \
		&& gecti "$t: AVB footer (NONE, salt, os_version, fingerprint) LineageOS'unkiyle aynı" \
		|| { kaldi "$t: AVB footer farklı"; diff "$T/lineage.avb" "$T/$t.avb"; }
	avbtool verify_image --image "$B" >/dev/null 2>&1 \
		&& gecti "$t: avbtool verify_image hash'i doğruluyor" || kaldi "$t: verify_image başarısız"
done
if [ "$LINEAGE_AYNI" = 1 ]; then
	cmp -s "$CIKTI/yamasiz/boot.img" "$LINEAGE_BOOT" \
		&& gecti "yamasız boot.img LineageOS'un boot.img'siyle bayt bayt aynı (paketleme birebir)" \
		|| kaldi "yamasız boot.img LineageOS'unkinden farklı"
else
	atlandi "yamasız boot.img = LineageOS boot.img"
fi
sonuc
