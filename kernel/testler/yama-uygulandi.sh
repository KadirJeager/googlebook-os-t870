#!/usr/bin/env bash
# Yama gerçekten uygulandı mı: depodaki .patch, kaynak ağacındaki yamalı commit'in
# kendisi; yamalı Image yamanın kodunu taşıyor, yamasız taşımıyor.
. "$(dirname "$0")/ortak.sh"
echo "yama-uygulandi"
gerekli "$CIKTI/yamali/Image" "derle yamali"
gerekli "$CIKTI/yamasiz/Image" "derle yamasiz"
P=("$KERNEL"/kaynak-yamalari/*.patch)
# Yamalı commit, tabanın üstüne depodaki yamaların sırayla uygulanmışıdır: her commit
# kendi .patch dosyasının birebir kendisi.
mapfile -t C < <(git -C "$SRC" rev-list --reverse "$KERNEL_COMMIT..$YAMALI_COMMIT" 2>/dev/null)
esit=1
[ "${#C[@]}" = "${#P[@]}" ] && [ "$(git -C "$SRC" rev-parse "${C[0]}^" 2>/dev/null)" = "$KERNEL_COMMIT" ] || esit=0
for i in "${!P[@]}"; do
	[ $esit = 1 ] || break
	git -C "$SRC" format-patch -1 "${C[$i]}" --zero-commit --no-signature --start-number $((i + 1)) --stdout \
		| cmp -s - "${P[$i]}" || esit=0
done
[ $esit = 1 ] \
	&& gecti "yamalı commit ${YAMALI_COMMIT:0:12} = ${KERNEL_COMMIT:0:12} + ${#P[@]} yama ($(cd "$KERNEL/kaynak-yamalari" && echo *.patch))" \
	|| kaldi "yamalı commit depodaki yamalarla eşleşmiyor (${#C[@]} commit, ${#P[@]} yama)"
for d in "folded back" "unfolded" "(BACK)" "tablet_mode"; do
	grep -aqF "$d" "$CIKTI/yamali/Image" && ! grep -aqF "$d" "$CIKTI/yamasiz/Image" \
		&& gecti "yamalı Image'da var, yamasızda yok: \"$d\"" \
		|| kaldi "\"$d\" dizesi beklenen Image'da değil"
done
f=$(git -C "$SRC" diff --name-only "$KERNEL_COMMIT" "$YAMALI_COMMIT" | tr '\n' ' ')
[ "$f" = "drivers/input/keyboard/hall_ic_logical.c drivers/input/keyboard/stm/stm32l0/stm32_pogo_i2c.c drivers/input/keyboard/stm/stm32l0/stm32_pogo_i2c.h drivers/input/keyboard/stm/stm32l0/stm32_pogo_keyboard.c drivers/input/keyboard/stm/stm32l0/stm32_pogo_touchpad.c include/linux/input/pogo_i2c_notifier.h " ] \
	&& gecti "yamalar yalnız hall_logical, pogo sürücüsü, klavye/touchpad ve notifier başlığına dokunuyor" \
	|| kaldi "yamanın dokunduğu dosyalar beklenenden farklı: $f"
sonuc
