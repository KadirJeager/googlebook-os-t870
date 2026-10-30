#!/usr/bin/env bash
# Googlebook OS T870: LineageOS 24 kernelini birebir yeniden derler, kılıf yamasını
# uygular ve tabletteki boot imajına aynı alanlarla yeniden paketler.
# Ne, neden ve nasıl geri dönüleceği: BENIOKU.md. Tablette deneme: CIHAZ-TESTI.md.
#
#   ./build.sh kaynak            kaynakları indirir ve kimlikleriyle doğrular
#   ./build.sh derle yamasiz     LineageOS kernelini olduğu gibi derler
#   ./build.sh derle yamali      kaynak-yamalari/*.patch uygulanmış kerneli derler
#   ./build.sh boot yamali       LineageOS zip'indeki boot.img alanlarıyla yeni boot imajı üretir
#   ./build.sh hepsi             kaynak + iki derleme + boot + testler
#
# Ağır iş (LTO'lu kernel derlemesi) makinede varsa ortak bellek ve disk kapısından geçer (kapi.sh).
set -euo pipefail

BURASI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IS="${GBOS_KERNEL_IS:-/var/tmp/gbos-kernel}"   # kaynak ağacı, araçlar ve derleme çıktıları
CIKTI="$BURASI/cikti"                           # git dışı: Image, boot imajı, Module.symvers
KAPI="$BURASI/../kapi.sh"                     # kaynak kapısı varsa ondan, yoksa doğrudan

# --- Sabit kaynaklar -----------------------------------------------------------
# Kernel: tablette çalışan sürüm dizesindeki commit (uname -r ... -g4478511c4b37).
KERNEL_DEPO=https://github.com/LineageOS/android_kernel_samsung_sm8250
KERNEL_COMMIT=4478511c4b3738ad117e674810c2dfde154724bf
# Yamalı ağacın commit'i: yama sabit kimlik ve tarihle uygulanır, kimlik tekrar üretilebilir.
YAMALI_COMMIT=fd7a9d55451e62e19e054643ca0b680146f95b40
YAMA_TARIHI="2026-10-24T22:00:00+0300"
YAMA_KIMLIK_AD=KadirJeager
YAMA_KIMLIK_EPOSTA=KadirJeager@users.noreply.github.com

# Clang: sürüm dizesindeki "based on r563880c" / llvm-project 5e96669f.
# LineageOS 24 manifesti AOSP depolarını android-17.0.0_r1 etiketinden alır.
AOSP=https://android.googlesource.com
CLANG_YOL="platform/prebuilts/clang/host/linux-x86/+archive/refs/tags/android-17.0.0_r1/clang-r563880c"
CLANG_BIN_AGAC=dd8a90cc0299ab2162b9597e0524ac2ccccab3da
CLANG_LIB_AGAC=4e2b8921f8a709b90b4b2a0056929d912b51a4d5
# LineageOS kernel.mk'nin kullandığı make ve soong_ui'nin PATH'in başına koyduğu
# prebuilts/build-tools/path/linux-x86 araçları, git blob kimlikleriyle. Kernel bu
# araçlarla derlenir: örneğin IKCONFIG arşivini toybox gzip sıkıştırır, GNU gzip
# aynı içerikten farklı bayt üretir.
BT_KOK="platform/prebuilts/build-tools/+/refs/tags/android-17.0.0_r1"
declare -A BT_BLOB=(
	[make]=553e164933536530486bc94b3845e445c1381ac1
	[gavinhoward-bc]=17ad8dda3ef817cb42eea81009d21ee56b5fb386
	[toybox]=8f1058231e89584be69ba75416bbadb136aafe10
	[one-true-awk]=07f8115590cabf52790672f8c4a056c1817f8bd0
	[bzip2]=b9ffebc8c346274ba567312f6072f4d94f228eff
	[xz]=d9f62f759653bf77a08456d9fdcb49c8caf6a5d9
)
# path/linux-x86 girdileri ve hedefleri (aynı etiketteki sembolik bağlar). python ve
# python3 py3-cmd'ye gider; bu kernelin derlemesi python çağırmadığı için alınmaz.
BT_PATH_TOYBOX=(basename cat chmod cmp comm cp cpio cut date dd dirname dos2unix du echo
	egrep env file find getconf getopt grep gzip head hostname id install ln ls md5sum
	mkdir mktemp mv nl od paste patch pgrep pkill printf ps pwd readlink realpath rm
	rmdir sed seq setsid sha1sum sha256sum sha512sum sleep sort stat tail tar tee test
	timeout touch tr true truncate uname uniq unix2dos wc which whoami xargs xxd)
declare -A BT_PATH_DIGER=([awk]=one-true-awk [bc]=gavinhoward-bc [bzcat]=bzip2
	[bzip2]=bzip2 [xz]=xz [xzcat]=xz)

# LineageOS android_device_samsung_sm8250-common + gts7lwifi BoardConfig*.mk:
#   TARGET_KERNEL_CONFIG := vendor/kona-perf_defconfig vendor/samsung/kona-sec-common.config
#   TARGET_KERNEL_CONFIG += vendor/samsung/gts7lwifi.config
DEFCONFIG=vendor/kona-perf_defconfig
CONFIG_PARCALARI=(vendor/samsung/kona-sec-common.config vendor/samsung/gts7lwifi.config)

# Tabletteki kernelin derlendiği yer ve kimlik (/proc/version ve Image içindeki
# entry.S yolu). Aynı yollar bwrap ile kurulur, çünkü kaynak yolu Image'a girer.
LOS_KOK=/lineage/lineage-24.0
LOS_KAYNAK=kernel/samsung/sm8250
LOS_OUT=$LOS_KOK/out/target/product/gts7lwifi/obj/KERNEL_OBJ
# Derleyicinin yolu da çıktıya girer: <stdarg.h> gibi clang'ın kendi başlıklarını
# kullanan dosyaların hata ayıklama bilgisi ThinLTO modül özetine, o da Image'daki
# sembol adlarına (.llvm.<özet>) yansır. LineageOS'taki yerinde bağlanır.
LOS_CLANG=$LOS_KOK/prebuilts/clang/host/linux-x86/clang-r563880c
LOS_BT=$LOS_KOK/prebuilts/build-tools/linux-x86/bin
LOS_BT_PATH=$LOS_KOK/prebuilts/build-tools/path/linux-x86
BUILD_USER=root
BUILD_HOST=6c12973f5f69
YAMASIZ_ZAMAN="Sun Oct 11 19:06:31 UTC 2026"
YAMALI_ZAMAN="Mon Oct 26 20:00:00 UTC 2026"   # son yama commit'inin tarihi (UTC)

# techpack/Kbuild alt dizinleri sırasız `find` ile listeler; bağlama sırası, dolayısıyla
# Image'daki bütün adresler, dizin okuma sırasına bağlıdır. LineageOS derleyicisindeki
# sıra tabletin /proc/kallsyms'inden okundu (msm_vidc < rmnet_perf < lpi_pinctrl <
# dp_usbpd). stub ve camera-bengal bu config'te nesne üretmez; *camera find dışıdır.
TECHPACK_SIRASI=(video data audio display stub camera-bengal)

# Tabletin boot bölümü; imaj alanları ve AVB footer'ı LineageOS'un boot.img'inden okunur.
BOOT_BOLUM_BOYUTU=71303168

SRC="$IS/src"
CLANG="$IS/clang-r563880c"
BT="$IS/build-tools/bin"
BT_PATH="$IS/build-tools/path"

hata() { echo "HATA: $*" >&2; exit 1; }
bilgi() { echo ">> $*" >&2; }
# Bir ağ kopması (zaman aşımı, mobil bağlantı) indirmeyi tek denemede düşürmesin: komut en çok
# dört kez denenir. Denenen komut kendi yarım çıktısını baştan yazar.
yeniden_dene() {
	local n=1
	until "$@"; do
		[ "$n" -ge 4 ] && return 1
		bilgi "indirme başarısız, yeniden deneniyor ($n/3)"
		sleep $((n * 15))
		n=$((n + 1))
	done
}

# LineageOS'un boot imajı: çalışma dizininin kurulduğu LineageOS zip'inden (../lineage-zip.py,
# sha256'sı lineage-hazirla.py'nin kaydıyla denetlenir). Kernelinin sürüm dizesindeki commit
# KERNEL_COMMIT değilse bildirilir: yeni nightly'lerin kerneli ilerlemiş olabilir; yamalar yine
# KERNEL_COMMIT üzerine uygulanır, boot'un kernel dışındaki alanları zip'ten gelir.
lineage_boot() {  # lineage_boot HEDEF
	local g surum
	python3 -I "$BURASI/../lineage-zip.py" cikar boot.img "$1" || hata "LineageOS boot imajı alınamadı"
	g=$(mktemp -d "$IS/lb-XXXXXX")
	unpack_bootimg --boot_img "$1" --out "$g" >/dev/null
	surum=$(strings "$g/kernel" | grep -m1 -o 'Linux version [^ ]*' || true)
	rm -rf "$g"
	if [[ "$surum" == *"-g${KERNEL_COMMIT:0:12}" ]]; then
		LINEAGE_BOOT_AYNI_KERNEL=1
	else
		LINEAGE_BOOT_AYNI_KERNEL=0
		bilgi "zip'teki kernel ($surum) ${KERNEL_COMMIT:0:12} değil; yamalı kernel ${KERNEL_COMMIT:0:12} üzerine kurulur"
	fi
}

# ------------------------------------------------------------------------------
kaynak_kernel() {
	if [ ! -d "$SRC/.git" ]; then
		mkdir -p "$SRC"
		git -C "$SRC" init -q
		git -C "$SRC" remote add origin "$KERNEL_DEPO"
	fi
	if ! git -C "$SRC" cat-file -e "$KERNEL_COMMIT^{commit}" 2>/dev/null; then
		bilgi "kernel kaynağı indiriliyor ($KERNEL_COMMIT, sığ)"
		yeniden_dene git -C "$SRC" fetch -q --depth 1 origin "$KERNEL_COMMIT" \
			|| hata "kernel kaynağı indirilemedi ($KERNEL_DEPO)"
	fi
	# git commit kimliği içeriğin SHA-1 özetidir: aynı kimlik aynı ağaç demektir.
	[ "$(git -C "$SRC" rev-parse "$KERNEL_COMMIT^{commit}")" = "$KERNEL_COMMIT" ] \
		|| hata "kernel commit doğrulanamadı"
}

clang_indir() {
	rm -rf "$CLANG/$1"
	mkdir -p "$CLANG/$1"
	curl -sSfL "$AOSP/$CLANG_YOL/$1.tar.gz" | tar xz -C "$CLANG/$1"
}

buildtool_indir() {
	curl -sSfL "$AOSP/$BT_KOK/linux-x86/bin/$1?format=TEXT" | base64 -d > "$BT/$1.tmp"
}

kaynak_clang() {
	local d agac beklenen
	for d in bin lib; do
		[ "$d" = bin ] && beklenen=$CLANG_BIN_AGAC || beklenen=$CLANG_LIB_AGAC
		if [ -d "$CLANG/$d" ] && [ -f "$CLANG/.$d.dogrulandi" ] \
			&& [ "$(cat "$CLANG/.$d.dogrulandi")" = "$beklenen" ]; then
			continue
		fi
		rm -f "$CLANG/.$d.dogrulandi"
		bilgi "clang-r563880c/$d indiriliyor"
		yeniden_dene clang_indir "$d" || hata "clang $d indirilemedi"
		agac=$(python3 -I "$BURASI/araclar/git-agac.py" "$CLANG/$d")
		[ "$agac" = "$beklenen" ] || hata "clang $d ağacı $agac, beklenen $beklenen"
		echo "$agac" > "$CLANG/.$d.dogrulandi"
	done
	"$CLANG/bin/clang" --version | head -1 | grep -q "based on r563880c" \
		|| hata "clang sürümü r563880c değil"
}

kaynak_buildtools() {
	local ad blob ad2
	mkdir -p "$BT"
	for ad in "${!BT_BLOB[@]}"; do
		if [ ! -x "$BT/$ad" ] || [ "$(git hash-object "$BT/$ad")" != "${BT_BLOB[$ad]}" ]; then
			bilgi "build-tools/$ad indiriliyor"
			yeniden_dene buildtool_indir "$ad" || hata "build-tools/$ad indirilemedi"
			chmod +x "$BT/$ad.tmp"
			mv "$BT/$ad.tmp" "$BT/$ad"
		fi
		blob=$(git hash-object "$BT/$ad")
		[ "$blob" = "${BT_BLOB[$ad]}" ] || hata "build-tools/$ad blob $blob, beklenen ${BT_BLOB[$ad]}"
	done
	rm -f "$BT/bc"   # eski düzen: bc artık path dizininde
	# Bağlar bwrap içindeki yola gider; toybox hangi aracın çağrıldığını adından anlar.
	rm -rf "$BT_PATH"
	mkdir -p "$BT_PATH"
	for ad in "${BT_PATH_TOYBOX[@]}"; do ln -s "$LOS_BT/toybox" "$BT_PATH/$ad"; done
	for ad in "${!BT_PATH_DIGER[@]}"; do ln -s "$LOS_BT/${BT_PATH_DIGER[$ad]}" "$BT_PATH/$ad"; done
	ad2=$(ls "$BT_PATH" | wc -l)
	[ "$ad2" = $(( ${#BT_PATH_TOYBOX[@]} + ${#BT_PATH_DIGER[@]} )) ] || hata "path dizini eksik: $ad2"
}

kaynak() {
	command -v bwrap >/dev/null || hata "bwrap (bubblewrap) gerekli"
	kaynak_kernel
	kaynak_clang
	kaynak_buildtools
	bilgi "kaynaklar doğrulandı"
}

# Kaynak ağacını istenen commit'e getirir. Yamalı ağaç, depodaki .patch dosyalarından
# sabit kimlik ve tarihle üretilir; sonuç commit'i YAMALI_COMMIT olmalıdır.
agaci_hazirla() {
	local tur=$1
	git -C "$SRC" checkout -q --detach "$KERNEL_COMMIT"
	git -C "$SRC" reset -q --hard
	git -C "$SRC" clean -qfdx
	if [ "$tur" = yamali ]; then
		GIT_AUTHOR_NAME=$YAMA_KIMLIK_AD GIT_AUTHOR_EMAIL=$YAMA_KIMLIK_EPOSTA \
		GIT_COMMITTER_NAME=$YAMA_KIMLIK_AD GIT_COMMITTER_EMAIL=$YAMA_KIMLIK_EPOSTA \
		GIT_COMMITTER_DATE=$YAMA_TARIHI \
			git -C "$SRC" am -q --committer-date-is-author-date "$BURASI"/kaynak-yamalari/*.patch
		[ "$(git -C "$SRC" rev-parse HEAD)" = "$YAMALI_COMMIT" ] \
			|| hata "yamalı commit $(git -C "$SRC" rev-parse HEAD), beklenen $YAMALI_COMMIT"
	fi
	techpack_sirala
}

# techpack alt dizinlerini TECHPACK_SIRASI ile yeniden oluşturur: btrfs ve tmpfs dizin
# girdilerini oluşturma sırasıyla okutur. Sonra Kbuild'in kullandığı find'ı aynen
# koşup sırayı doğrular; karma sıralı bir dosya sisteminde (ext4) derleme durur.
techpack_sirala() {
	local tp="$SRC/techpack" d bulunan beklenen
	for d in "${TECHPACK_SIRASI[@]}"; do
		[ -d "$tp/$d" ] || hata "techpack/$d yok"
		mv "$tp/$d" "$tp/.$d.sirala"
		mv "$tp/.$d.sirala" "$tp/$d"
	done
	bulunan=$(find "$tp" -maxdepth 1 -mindepth 1 -type d -not -name ".*" -not -name "*camera" -printf '%f ')
	beklenen="${TECHPACK_SIRASI[*]} "
	[ "$bulunan" = "$beklenen" ] \
		|| hata "techpack sırası '$bulunan', beklenen '$beklenen' ($IS dizin sırasını korumuyor)"
}

# Kökü kopyalayan bir mount ad alanı kurar ve LineageOS'un derleme yollarını
# (/lineage/lineage-24.0/...) buraya bağlar. Gerçek köke hiçbir şey yazılmaz.
bwrap_calistir() {
	local out=$1; shift
	local arg=() g ad
	for g in /*; do
		ad=${g#/}
		case "$ad" in proc|dev|lineage) continue ;; esac
		if [ -L "$g" ]; then
			arg+=(--symlink "$(readlink "$g")" "$g")
		elif [ -d "$g" ]; then
			arg+=(--dev-bind "$g" "$g")
		fi
	done
	bwrap "${arg[@]}" --proc /proc --dev-bind /dev /dev \
		--bind "$SRC" "$LOS_KOK/$LOS_KAYNAK" \
		--bind "$out" "$LOS_OUT" \
		--ro-bind "$CLANG" "$LOS_CLANG" \
		--ro-bind "$BT" "$LOS_BT" \
		--ro-bind "$BT_PATH" "$LOS_BT_PATH" \
		--chdir "$LOS_KOK" -- "$@"
}

# LineageOS vendor/lineage/build/tasks/kernel.mk ve config/BoardConfigKernel.mk'nin
# kurduğu make çağrısı (TARGET_KERNEL_CLANG_PATH, LLVM=1 LLVM_IAS=1, CC=clang LD=ld.lld,
# CLANG_TRIPLE=aarch64-linux-gnu-, -C kernel/samsung/sm8250 O=<mutlak out>).
make_kernel() {
	local zaman=$1; shift
	bwrap_calistir "$OUT" env \
		PATH="$LOS_BT_PATH:$LOS_CLANG/bin:/usr/bin:/bin" \
		LD_LIBRARY_PATH="$LOS_CLANG/lib" \
		HIP_PATH=none \
		KBUILD_BUILD_USER=$BUILD_USER KBUILD_BUILD_HOST=$BUILD_HOST \
		KBUILD_BUILD_TIMESTAMP="$zaman" KBUILD_BUILD_VERSION=1 \
		LC_ALL=C TZ=UTC \
		"$LOS_BT/make" -j"${GBOS_KERNEL_JOBS:-$(nproc)}" -C "$LOS_KAYNAK" O="$LOS_OUT" ARCH=arm64 \
			CLANG_TRIPLE=aarch64-linux-gnu- CC=clang LD=ld.lld LLVM=1 LLVM_IAS=1 "$@"
}

config_uret() {
	local p
	mkdir -p "$OUT"
	# Hata ayıklama bilgili nesneler ~5 GB tutar; btrfs'te sıkıştırılarak yazılır.
	chattr +c "$OUT" 2>/dev/null || true
	cp "$SRC/arch/arm64/configs/$DEFCONFIG" "$OUT/.config"
	make_kernel "$ZAMAN" olddefconfig
	# MERGE_ALL_KERNEL_CONFIGS_AT_ONCE=false: parçalar sırayla birleştirilir.
	for p in "${CONFIG_PARCALARI[@]}"; do
		bwrap_calistir "$OUT" bash -c "cd '$LOS_OUT' && '$LOS_KOK/$LOS_KAYNAK/scripts/kconfig/merge_config.sh' -m -O '$LOS_OUT' '$LOS_OUT/.config' '$LOS_KOK/$LOS_KAYNAK/arch/arm64/configs/$p'" >/dev/null
		make_kernel "$ZAMAN" olddefconfig
	done
}

derle() {
	local tur=${1:-}
	case "$tur" in
		yamasiz) ZAMAN=$YAMASIZ_ZAMAN ;;
		yamali) ZAMAN=$YAMALI_ZAMAN ;;
		*) hata "kullanım: build.sh derle yamasiz|yamali" ;;
	esac
	kaynak
	OUT="$IS/out-$tur"
	rm -rf "$OUT"
	agaci_hazirla "$tur"
	config_uret
	bilgi "derleniyor: $tur ($(cat "$OUT/include/config/kernel.release" 2>/dev/null || true))"
	make_kernel "$ZAMAN" Image
	kheaders_sabitle "$tur"
	mkdir -p "$CIKTI/$tur"
	cp "$OUT/arch/arm64/boot/Image" "$OUT/.config" "$OUT/Module.symvers" \
		"$OUT/System.map" "$OUT/include/config/kernel.release" "$CIKTI/$tur/"
	( cd "$CIKTI/$tur" && sha256sum Image > Image.sha256 )
	bilgi "Image: $CIKTI/$tur/Image ($(cut -d' ' -f1 "$CIKTI/$tur/Image.sha256"))"
	# Out dizini birkaç GB'dır ve kök diskte yer dardır; gereken her şey CIKTI'ya
	# kopyalandı. Artımlı derleme için GBOS_KERNEL_OUT_TUT=1 ile tutulur.
	[ "${GBOS_KERNEL_OUT_TUT:-0}" = 1 ] || rm -rf "$OUT"
}

# CONFIG_IKHEADERS arşivi, başlık içeriği dışında derleme anının zamanını, kullanıcıyı
# ve dizin sırasını taşır (araclar/kheaders.py). Yamasız derlemede tabletteki
# kernel'in arşivi, içeriği bizim ürettiğimizle birebir aynıysa yerine konur; içerik
# farklıysa derleme durur. Yamalı derlemede (yama bir başlığı değiştirir) arşiv sıralı,
# root:root ve derleme zamanıyla yeniden paketlenir. İki durumda da Image tekrar
# üretilebilir olur; kheaders.o yeniden derlenip vmlinux yeniden bağlanır.
kheaders_sabitle() {
	local tur=$1 arsiv="$OUT/kernel/kheaders_data.tar.xz" yeni="$OUT/kheaders.yeni.tar.xz"
	local kh="$BURASI/araclar/kheaders.py" yedek gecici
	[ -f "$arsiv" ] || hata "kheaders arşivi yok: $arsiv"
	if [ "$tur" = yamasiz ]; then
		gecici=$(mktemp -d "$IS/kh-XXXXXX")
		yedek="$gecici/lineage-boot.img"
		lineage_boot "$yedek"
		[ "$LINEAGE_BOOT_AYNI_KERNEL" = 1 ] \
			|| hata "yamasız derleme LineageOS kernelinin aynısını üretir; zip'teki kernel ${KERNEL_COMMIT:0:12} değil"
		unpack_bootimg --boot_img "$yedek" --out "$gecici" >/dev/null
		python3 -I "$kh" cikar "$gecici/kernel" "$yeni" >&2
		rm -rf "$gecici"
		python3 -I "$kh" karsilastir "$arsiv" "$yeni" >&2 \
			|| hata "kheaders içeriği tabletteki kernelinkinden farklı"
	else
		python3 -I "$kh" normalle "$arsiv" "$yeni" "$(date -u -d "$ZAMAN" +%s)"
	fi
	mv "$yeni" "$arsiv"
	# gen_kheaders.sh arşivi yeniden üretmesin: son satır arşivin md5'idir.
	sed -i '$d' "$OUT/kernel/kheaders.md5"
	md5sum "$arsiv" | cut -d' ' -f1 >> "$OUT/kernel/kheaders.md5"
	make_kernel "$ZAMAN" Image
	python3 -I "$kh" cikar "$OUT/arch/arm64/boot/Image" "$OUT/kheaders.kontrol.tar.xz" >/dev/null
	cmp -s "$OUT/kheaders.kontrol.tar.xz" "$arsiv" || hata "kheaders Image'a girmedi"
	rm -f "$OUT/kheaders.kontrol.tar.xz"
}

# Ağır derlemeyi ortak bellek/disk kapısından geçirir. Derleme kendi systemd
# kapsamında koşar; bittiğinde kapsamın bellek tepesi (memory.peak) yazılır ki
# --gb ölçülmüş değerle verilsin. Ölçüm (-j8, 24 Eki 2026): bkz. BENIOKU.md.
derle_kapidan() {
	"$KAPI" --gb "${GBOS_KERNEL_GB:-11}" --tur derleme \
		--ad "GooglebookOS: T870 kernel derlemesi ($1)" \
		--disk-gb "${GBOS_KERNEL_DISK_GB:-4}" --yol "$IS" \
		${GBOS_KERNEL_TABAN:+--taban "$GBOS_KERNEL_TABAN"} -- \
		systemd-run --user --scope -q -- "$BURASI/build.sh" derle-olcerek "$1"
}

derle_olcerek() {
	local cg
	derle "$1"
	cg="/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)"
	[ -r "$cg/memory.peak" ] && bilgi "bellek tepesi: $(( $(cat "$cg/memory.peak") / 1048576 )) MiB" || true
}

# LineageOS zip'indeki boot imajını açar; aynı başlık sürümü,
# cmdline, ramdisk, dtb, adresler ve AVB hash footer'ıyla yeni kerneli paketler.
boot() {
	local tur=${1:-yamali} yedek gecici args salt props=() satir
	[ -f "$CIKTI/$tur/Image" ] || hata "önce: build.sh derle $tur"
	gecici=$(mktemp -d "$IS/boot-XXXXXX")
	trap 'rm -rf "$gecici"' RETURN
	yedek="$gecici/lineage-boot.img"
	lineage_boot "$yedek"
	unpack_bootimg --boot_img "$yedek" --out "$gecici/ac" --format=mkbootimg -0 > "$gecici/args"
	# Kernel dışındaki her alan eskisinden gelir; yalnız --kernel değişir.
	python3 -I - "$gecici/args" "$CIKTI/$tur/Image" "$gecici/args.yeni" <<-'PY'
		import sys
		a = open(sys.argv[1], "rb").read().split(b"\0")
		if a and a[-1] == b"":
		    a.pop()
		i = a.index(b"--kernel")
		a[i + 1] = sys.argv[2].encode()
		open(sys.argv[3], "wb").write(b"\0".join(a) + b"\0")
	PY
	( cd "$gecici" && xargs -0 mkbootimg --output "$gecici/boot.img" < args.yeni )
	salt=$(avbtool info_image --image "$yedek" | awk '/Salt:/{print $2; exit}')
	while IFS= read -r satir; do
		props+=(--prop "$satir")
	done < <(avbtool info_image --image "$yedek" | sed -n "s/^ *Prop: \(.*\) -> '\(.*\)'$/\1:\2/p")
	avbtool info_image --image "$yedek" | grep -q "Algorithm: *NONE" \
		|| hata "LineageOS boot imajı imzalı; imzasız paketleme bu imaja uymaz"
	avbtool add_hash_footer --image "$gecici/boot.img" --partition_size "$BOOT_BOLUM_BOYUTU" \
		--partition_name boot --hash_algorithm sha256 --salt "$salt" --algorithm NONE \
		--rollback_index 0 "${props[@]}"
	mkdir -p "$CIKTI/$tur"
	mv "$gecici/boot.img" "$CIKTI/$tur/boot.img"
	( cd "$CIKTI/$tur" && sha256sum boot.img > boot.img.sha256 )
	bilgi "boot: $CIKTI/$tur/boot.img ($(cut -d' ' -f1 "$CIKTI/$tur/boot.img.sha256"))"
}

hepsi() {
	kaynak
	derle_kapidan yamasiz
	derle_kapidan yamali
	boot yamasiz
	boot yamali
	"$BURASI/testler/hepsi.sh"
}

komut=${1:-}
shift || true
case "$komut" in
	kaynak) kaynak ;;
	derle) derle "$@" ;;
	derle-kapidan) derle_kapidan "$@" ;;
	derle-olcerek) derle_olcerek "$@" ;;
	boot) boot "$@" ;;
	hepsi) hepsi ;;
	*) sed -n '2,12p' "$0"; exit 2 ;;
esac
