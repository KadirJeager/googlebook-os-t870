#!/usr/bin/env bash
# Modül uyumu. Tablette yüklenebilir modül yok (beklenen/cihaz-moduller.txt, config'de
# =m yok, boot ramdisk'inde .ko yok); yine de dışa aktarılan ABI'nin modversions
# CRC'leri sınanır: tabletteki kernel'in __kcrctab tabloları = yamasız derlemenin
# Module.symvers'ı, ve yama hiçbir CRC'yi ya da dışa aktarılan sembolü değiştirmez.
. "$(dirname "$0")/ortak.sh"
echo "modul-crc"
for t in yamasiz yamali; do
	gerekli "$CIKTI/$t/Module.symvers" "derle $t"
	gerekli "$CIKTI/$t/System.map" "derle $t"
done
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
lineage_boot_gerekli
boot_ac "$LINEAGE_BOOT" "$T/lineage"

grep -q '^/proc/modules satır: 0$' "$BEKLENEN/cihaz-moduller.txt" \
	&& ! grep -E '^/[^:]*: [1-9][0-9]* \.ko' "$BEKLENEN/cihaz-moduller.txt" >/dev/null \
	&& gecti "tablette yüklü modül ve .ko dosyası yok (beklenen/cihaz-moduller.txt)" \
	|| kaldi "tablette modül kaydı var"
n=$(gzip -dc "$T/lineage/ramdisk" | cpio -it --quiet 2>/dev/null | grep -c '\.ko$' || true)
[ "$n" = 0 ] && gecti "boot ramdisk'inde .ko yok" || kaldi "boot ramdisk'inde $n .ko var"

K="$KERNEL/araclar/kcrctab.py"
# Tabletteki kernel'in System.map'i yok (kptr_restrict=2); yamasız derlemenin CRC
# tabloları tabletteki Image'da bayt bayt aranır. Bulunması, 13 bini aşkın CRC'nin
# aynı sırada aynı olduğunu gösterir; yerleşimden bağımsızdır.
if [ "$LINEAGE_AYNI" != 1 ]; then
	atlandi "CRC tabloları LineageOS kernelinde"
elif python3 -I "$K" "$CIKTI/yamasiz/System.map" "$CIKTI/yamasiz/Image" "$T/lineage/kernel" > "$T/bul"; then
	gecti "LineageOS kerneli yamasız derlemenin __kcrctab ve __kcrctab_gpl tablolarını aynen taşıyor ($(awk '{s+=$2} END {print s}' "$T/bul") CRC)"
else
	kaldi "CRC tablosu LineageOS kernelinde yok: $(cat "$T/bul")"
fi
python3 -I "$K" "$CIKTI/yamasiz/System.map" "$CIKTI/yamasiz/Image" | sort > "$T/crc.yamasiz"
awk '{print $1}' "$CIKTI/yamasiz/Module.symvers" | sort > "$T/symvers.crc"
cmp -s "$T/crc.yamasiz" "$T/symvers.crc" \
	&& gecti "yamasız Image'ın CRC tablosu = Module.symvers ($(wc -l < "$T/symvers.crc") sembol)" \
	|| kaldi "yamasız Image CRC tablosu Module.symvers ile uyuşmuyor"
cmp -s "$CIKTI/yamasiz/Module.symvers" "$CIKTI/yamali/Module.symvers" \
	&& gecti "yamalı Module.symvers = yamasız (sembol, CRC, dışa aktarım türü aynı)" \
	|| { kaldi "yama dışa aktarılan ABI'yi değiştirdi"; diff "$CIKTI/yamasiz/Module.symvers" "$CIKTI/yamali/Module.symvers" | head; }
if [ "$LINEAGE_AYNI" != 1 ]; then
	atlandi "yamalı CRC tabloları LineageOS kernelinde"
elif python3 -I "$K" "$CIKTI/yamali/System.map" "$CIKTI/yamali/Image" "$T/lineage/kernel" > "$T/bul.yamali"; then
	gecti "yamalı Image'ın CRC tabloları LineageOS kernelinde aynen var"
else
	kaldi "yamalı Image CRC tablosu LineageOS'unkinden farklı: $(cat "$T/bul.yamali")"
fi
sonuc
