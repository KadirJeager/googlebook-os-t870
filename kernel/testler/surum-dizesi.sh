#!/usr/bin/env bash
# Sürüm dizesi ve bayt denkliği: yamasız Image tabletteki kernel'in birebir aynısı
# olmalı; yamalı Image'ın sürüm dizesi yalnız commit ve tarih kısmında ayrılmalı.
. "$(dirname "$0")/ortak.sh"
echo "surum-dizesi"
gerekli "$CIKTI/yamasiz/Image" "derle yamasiz"
gerekli "$CIKTI/yamali/Image" "derle yamali"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
lineage_boot_gerekli
boot_ac "$LINEAGE_BOOT" "$T/lineage"
bek=$(cat "$BEKLENEN/proc-version")
surum() { grep -aoE 'Linux version [[:print:]]+' "$1" | head -1; }

if [ "$LINEAGE_AYNI" = 1 ]; then
	[ "$(surum "$T/lineage/kernel")" = "$bek" ] \
		&& gecti "LineageOS boot'undaki kernel'in sürüm dizesi = tabletin /proc/version" \
		|| kaldi "LineageOS kernel sürüm dizesi /proc/version'dan farklı"
fi
s=$(surum "$CIKTI/yamasiz/Image")
[ "$s" = "$bek" ] && gecti "yamasız Image sürüm dizesi = /proc/version" \
	|| { kaldi "yamasız sürüm dizesi farklı"; echo "    $s"; }
if [ "$LINEAGE_AYNI" != 1 ]; then
	atlandi "yamasız Image = LineageOS kernel"
elif cmp -s "$CIKTI/yamasiz/Image" "$T/lineage/kernel"; then
	gecti "yamasız Image LineageOS kerneliyle bayt bayt aynı ($(sha256sum < "$T/lineage/kernel" | cut -c1-16)...)"
else
	kaldi "yamasız Image LineageOS kernelinden farklı ($(cmp "$CIKTI/yamasiz/Image" "$T/lineage/kernel" | head -1))"
fi
y=$(surum "$CIKTI/yamali/Image")
kisa=${YAMALI_COMMIT:0:12}
beklenen_y=$(echo "$bek" | sed -e "s/-g${KERNEL_COMMIT:0:12}/-g$kisa/" -e "s/$YAMASIZ_ZAMAN/$YAMALI_ZAMAN/")
[ "$y" = "$beklenen_y" ] && gecti "yamalı sürüm dizesi: yalnız -g$kisa ve derleme tarihi farklı" \
	|| { kaldi "yamalı sürüm dizesi beklenmedik"; echo "    $y"; }
sonuc
