#!/usr/bin/env bash
# Yapılandırma denkliği: tabletin /proc/config.gz'si (beklenen/), LineageOS zip'indeki
# boot'un kernel'inin gömülü config'i ve iki derlemenin .config'i satır satır aynı olmalı.
. "$(dirname "$0")/ortak.sh"
echo "config-denk"
BEK="$(ls "$BEKLENEN"/config-* | head -1)"
gerekli "$CIKTI/yamasiz/.config" "derle yamasiz"
gerekli "$CIKTI/yamali/.config" "derle yamali"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

lineage_boot_gerekli
boot_ac "$LINEAGE_BOOT" "$T/lineage"
"$SRC/scripts/extract-ikconfig" "$T/lineage/kernel" > "$T/lineage.config"
if [ "$LINEAGE_AYNI" = 1 ]; then
	cmp -s "$T/lineage.config" "$BEK" \
		&& gecti "LineageOS boot'unun kernel config'i = tabletin /proc/config.gz" \
		|| kaldi "LineageOS boot config'i tabletinkinden farklı"
else
	atlandi "LineageOS boot config'i = tabletin config'i"
fi

if cmp -s "$CIKTI/yamasiz/.config" "$BEK"; then
	gecti "yamasız derlemenin .config'i = tabletin config'i ($(wc -l < "$BEK") satır)"
else
	kaldi "yamasız .config farklı:"; diff "$BEK" "$CIKTI/yamasiz/.config" | head -20
fi
"$SRC/scripts/extract-ikconfig" "$CIKTI/yamasiz/Image" > "$T/yamasiz.config"
cmp -s "$T/yamasiz.config" "$BEK" \
	&& gecti "yamasız Image'a gömülü config = tabletin config'i" \
	|| kaldi "yamasız Image'a gömülü config farklı"

if cmp -s "$CIKTI/yamali/.config" "$BEK"; then
	gecti "yamalı derlemenin .config'i = tabletin config'i (yama config değiştirmez)"
else
	kaldi "yamalı .config farklı:"; diff "$BEK" "$CIKTI/yamali/.config" | head -20
fi
grep -q '=m$' "$BEK" && kaldi "config'de modül (=m) var" \
	|| gecti "config'de modül olarak derlenen seçenek yok (=m: 0)"
sonuc
