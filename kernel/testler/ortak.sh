# Testlerin ortak yolları ve yardımcıları; testler/hepsi.sh ve tek tek testler kaynaklar.
set -u
KERNEL="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IS="${GBOS_KERNEL_IS:-/var/tmp/gbos-kernel}"
SRC="$IS/src"
CIKTI="$KERNEL/cikti"
BEKLENEN="$KERNEL/beklenen"
# LineageOS's boot.img from the zip the work directory was built from (lineage-zip.py), taken
# out once per zip: the file name carries the zip's SHA-256 recorded in KAYNAK
KAYNAK_ZIP="${GBOS_WORK:-/var/tmp/mica_work}/lineage/bolumler/KAYNAK"
LINEAGE_BOOT="$IS/lineage-boot-$(grep -o 'sha256=[0-9a-f]*' "$KAYNAK_ZIP" 2>/dev/null | cut -c8-23).img"
eval "$(grep -E '^(KERNEL_COMMIT|YAMALI_COMMIT|BOOT_BOLUM_BOYUTU|YAMASIZ_ZAMAN|YAMALI_ZAMAN)=' "$KERNEL/build.sh")"

GECTI=0
KALDI=0
gecti() { echo "  GEÇTİ  $*"; GECTI=$((GECTI + 1)); }
kaldi() { echo "  KALDI  $*"; KALDI=$((KALDI + 1)); }
sonuc() {
	echo "  -- $GECTI geçti, $KALDI kaldı"
	[ "$KALDI" -eq 0 ]
}
gerekli() { [ -e "$1" ] || { echo "  EKSİK  $1 (önce build.sh $2)"; exit 2; }; }

# LineageOS boot'unu hazırlar ve LINEAGE_AYNI'yı koyar: 1 ise zip'teki kernel KERNEL_COMMIT'in
# kendisidir (tabletteki /proc/version, beklenen/), onunla bayt karşılaştırmaları yapılır
lineage_boot_gerekli() {
	[ -f "$KAYNAK_ZIP" ] || { echo "  EKSİK  $KAYNAK_ZIP (önce lineage-hazirla.py ZIP)"; exit 2; }
	if [ ! -s "$LINEAGE_BOOT" ]; then
		python3 -I "$KERNEL/../lineage-zip.py" cikar boot.img "$LINEAGE_BOOT" \
			|| { echo "  EKSİK  LineageOS boot.img (lineage-zip.py)"; exit 2; }
	fi
	LINEAGE_AYNI=0
	strings "$LINEAGE_BOOT" | grep -q "Linux version [^ ]*-g${KERNEL_COMMIT:0:12} " && LINEAGE_AYNI=1
	return 0
}
atlandi() { echo "  ATLANDI $* (zip'teki kernel ${KERNEL_COMMIT:0:12} değil)"; }

# Boot imajındaki kernel'i çıkarır: $1 imaj, $2 hedef dizin
boot_ac() {
	unpack_bootimg --boot_img "$1" --out "$2" >/dev/null
}
