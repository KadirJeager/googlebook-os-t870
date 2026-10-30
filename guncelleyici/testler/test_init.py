"""gbos-guncelleyici.rc: files recovery leaves in /cache/gbos carry no SELinux label; the boot
guard (gbos_guncelleyici domain) cannot read unlabeled files, so init relabels the directory
before it starts the guard (found on the tablet, 24 Oct 2026). --force, because restorecon skips a
directory whose stored file_contexts digest has not changed, which left the files of the second
update unlabeled."""
import os
import subprocess

import pytest

RC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "cihaz", "system", "etc", "init", "gbos-guncelleyici.rc")


def _post_fs_data():
    lines, inside = [], False
    for line in open(RC):
        if line.startswith("on "):
            inside = line.strip() == "on post-fs-data"
            continue
        if line.startswith("service "):
            inside = False
        if inside and line.strip() and not line.strip().startswith("#"):
            lines.append(line.strip())
    return lines


def test_cache_bekciden_once_etiketlenir():
    satirlar = _post_fs_data()
    assert "restorecon_recursive --force /cache/gbos" in satirlar
    assert satirlar.index("restorecon_recursive --force /cache/gbos") < satirlar.index("start gbos_bekci")
    assert satirlar.index("mkdir /cache/gbos 0700 root root") < satirlar.index("restorecon_recursive --force /cache/gbos")


IS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "cihaz", "system", "bin", "gbos-guncelleyici-is")
PC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "cihaz", "property_contexts")


def test_onay_denemede_tutulur():
    """vold commits nothing while persist.vold.dont_commit_checkpoint is 1: kur sets it before
    starting the checkpoint, the guard clears it right before its own commitChanges (on the
    tablet Play services committed ~2 s after boot_completed, 24 Oct 2026)."""
    s = open(IS).read()
    kur = s.index("hold_commit\n        [ \"$(getprop persist.vold.dont_commit_checkpoint)\" = 1 ]")
    assert kur < s.index("vdc checkpoint startCheckpoint")
    commit = s[s.index("commit_checkpoint() {"):]
    assert commit.index("release_commit") < commit.index("vdc checkpoint commitChanges")



def test_bekci_denemede_onayi_yeniden_tutar():
    """A trial started without the hold (installed by an older kur) is held again at
    post-fs-data, before anything can commit (24 Oct 2026: such a trial was committed at
    boot_completed)."""
    s = open(IS).read()
    trial = s[s.index("deneme açılışı $count"):s.index("wait_boot $BOOT_TIMEOUT ||")]
    assert "hold_commit" in trial
    assert 'persist.vold.dont_commit_checkpoint)" != 1' in trial


def test_bekci_erken_onayi_bildirir():
    """vold.checkpoint_committed set before the guard's own commit means the trial was
    committed early; the guard logs it before committing."""
    s = open(IS).read()
    trial = s[s.index("healthy || request_rollback bekci"):]
    assert trial.index("vold.checkpoint_committed") < trial.index("commit_checkpoint ||")

def test_ozellik_etiketi():
    satirlar = [l.split() for l in open(PC) if l.strip() and not l.startswith("#")]
    assert satirlar == [["persist.vold.dont_commit_checkpoint", "u:object_r:gbos_vold_commit_prop:s0",
                         "exact", "bool"]]


def _checkpoint_fonksiyonlari():
    s = open(IS).read()
    return s[s.index("vdc_ask() {"):s.index("# --- durum")]


@pytest.mark.parametrize("vdc_need,onaylar,rc,kayit", [
    (1, True, 0, "veri checkpoint'i onaylandı"),   # vdc exit 1: a checkpoint is pending
    (0, False, 0, "bekleyen checkpoint yok"),      # exit 0: none
    (64, False, 1, "vdc checkpoint durumunu bildirmedi"),
])
def test_commit_checkpoint_vdc_yanitini_dogru_okur(tmp_path, vdc_need, onaylar, rc, kayit):
    """vdc reports needsCheckpoint with its exit status (system/vold/vdc.cpp). On the tablet the
    guard read it inverted and never committed a pending checkpoint itself (24 Oct 2026)."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "checkpoint").touch()
    for ad, govde in {
        "vdc": f'echo "$*" >> {tmp_path}/vdc.log\n'
               f'[ "$2" = needsCheckpoint ] && exit {vdc_need}\n'
               f'[ "$2" = commitChanges ] && echo 1 > {tmp_path}/committed\nexit 0',
        "getprop": f'[ "$1" = vold.checkpoint_committed ] && cat {tmp_path}/committed 2>/dev/null\n'
                   f'[ "$1" = persist.vold.dont_commit_checkpoint ] && cat {tmp_path}/dc 2>/dev/null\nexit 0',
        "setprop": f'echo "$2" > {tmp_path}/dc',
        "sync": "exit 0",
    }.items():
        (bin_ / ad).write_text("#!/bin/sh\n" + govde + "\n")
        (bin_ / ad).chmod(0o755)
    (tmp_path / "dc").write_text("1\n")
    betik = (f'CACHE={tmp_path}/cache\nkaydet() {{ echo "$*" >> {tmp_path}/kayit; }}\n'
             + _checkpoint_fonksiyonlari() + "\ncommit_checkpoint\n")
    r = subprocess.run(["sh", "-c", betik], env={"PATH": f"{bin_}:/usr/bin:/bin"},
                       capture_output=True, text=True)
    assert r.returncode == rc, r.stderr
    assert kayit in (tmp_path / "kayit").read_text()
    komutlar = (tmp_path / "vdc.log").read_text()
    assert ("commitChanges" in komutlar) == onaylar
    if onaylar:
        assert (tmp_path / "dc").read_text().strip() == "0"


def test_yeni_kurulum_bildirimi_sifirlar():
    """kur clears the notice marker with the result: a marker from an earlier install kept the
    rollback state from being reported (tablet, 24 Oct 2026)."""
    s = open(IS).read()
    assert 'rm -f "$CACHE/sonuc" "$CACHE/sonuc.bildirildi"' in s
    assert s.index('rm -f "$CACHE/sonuc" "$CACHE/sonuc.bildirildi"') < s.index("vdc checkpoint startCheckpoint")


def test_geri_donuste_basarisiz_kume_silinir():
    s = open(IS).read()
    dal = s[s.index("geri-donuldu*)"):s.index("kurulamadi*)")]
    assert 'rm -rf "$YENI"' in dal and "kur.zip" in dal
