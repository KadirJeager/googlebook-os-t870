"""Feature XMLs: apply_patches.reconcile_features against the tablet's vendor declarations.

mica (built for Dell laptops) hides features the tablet has. Without android.software.device_admin
Play services disables its Find My Device module, so Find Hub listed the tablet but could not turn
it on; android.hardware.location.gps hid the working GNSS receiver (22 Oct 2026). Hardware the
vendor declares is made available by rule, software features only by decision, and a difference
without a rule or decision stops the build."""
import io
import os
import re
import shutil
import xml.etree.ElementTree as ET

import pytest

from conftest import GERCEK_WORK, MICA, EROFS_BIN, gerekli, oku, yaz

FEATURES = "system/etc/permissions/features.xml"
DEVICE_MANAGEMENT = "system/etc/permissions/device_management_features.xml"


def ozellikler(metin):
    kok = ET.fromstring(re.sub(r"^\s*<\?xml[^>]*\?>", "", metin))
    return ({e.get("name") for e in kok.iter("feature")},
            {e.get("name") for e in kok.iter("unavailable-feature")})


@pytest.fixture
def agac(ap, monkeypatch, gecici):
    """Empty partition trees (only the feature directories) and the real vendor stage."""
    gerekli(os.path.join(MICA, "system_a.erofs"), os.path.join(EROFS_BIN, "dump.erofs"),
            os.path.join(GERCEK_WORK, "lineage/vendor_features/KAYNAK"))
    for var in ("SYS_DIR", "SYSEXT_DIR", "PROD_DIR"):
        monkeypatch.setattr(ap, var, os.path.join(gecici, var))
    for var, _, rel in ap.FEATURE_DIRS:
        os.makedirs(os.path.join(gecici, var, rel), exist_ok=True)
    monkeypatch.setattr(ap, "STAGE_VENDOR_FEATURES",
                        os.path.join(GERCEK_WORK, "lineage/vendor_features"))
    return lambda rel: oku(os.path.join(gecici, "SYS_DIR", rel))


def test_find_hub_ve_gps_acilir(ap, agac):
    ap.reconcile_features()
    var, yok = ozellikler(agac(DEVICE_MANAGEMENT))
    p_var, p_yok = ozellikler(ap.pristine_file(DEVICE_MANAGEMENT).decode())
    assert yok == p_yok - {"android.software.device_admin"}
    assert "android.software.managed_users" in yok
    assert var == p_var and "com.google.desktop.restricted_managed_accounts" in var
    _, yok = ozellikler(agac(FEATURES))
    assert "android.hardware.location.gps" not in yok


def test_tekrar_calisinca_ayni(ap, agac):
    ap.reconcile_features()
    ilk = agac(DEVICE_MANAGEMENT), agac(FEATURES)
    ap.reconcile_features()
    assert (agac(DEVICE_MANAGEMENT), agac(FEATURES)) == ilk


def test_geri_alinan_karar_dosyayi_eski_haline_dondurur(ap, agac, monkeypatch):
    ap.reconcile_features()
    monkeypatch.setitem(ap.SOFTWARE_FEATURE_DECISIONS, "android.software.device_admin", False)
    ap.reconcile_features()
    assert agac(DEVICE_MANAGEMENT) == ap.pristine_file(DEVICE_MANAGEMENT).decode()


def test_kararsiz_yazilim_farki_durdurur(ap, agac, monkeypatch):
    monkeypatch.delitem(ap.SOFTWARE_FEATURE_DECISIONS, "android.software.managed_users")
    with pytest.raises(SystemExit, match="hides android.software.managed_users"):
        ap.reconcile_features()


def test_vendor_bildirmeyen_donanim_gizli_kalir(ap, agac, monkeypatch, gecici):
    """The same run with a vendor stage that lacks gps: mica's line must stay."""
    stage = os.path.join(gecici, "vendor_features")
    shutil.copytree(ap.STAGE_VENDOR_FEATURES, stage)
    os.remove(os.path.join(stage, "android.hardware.location.gps.xml"))
    for name in (n for n in os.listdir(stage) if n.endswith(".xml")):
        assert "android.hardware.location.gps" not in ozellikler(oku(os.path.join(stage, name)))[0], \
            f"{name} also declares gps"
    monkeypatch.setattr(ap, "STAGE_VENDOR_FEATURES", stage)
    ap.reconcile_features()
    _, yok = ozellikler(agac(FEATURES))
    assert "android.hardware.location.gps" in yok


def test_yalniz_micada_olan_donanim_bildirilmeli(ap, agac, monkeypatch):
    monkeypatch.setattr(ap, "MICA_ONLY_HARDWARE", set())
    with pytest.raises(SystemExit, match="declares android.hardware.type.pc"):
        ap.reconcile_features()


def test_vendor_asamasi_yoksa_durur(ap, agac, monkeypatch, gecici):
    monkeypatch.setattr(ap, "STAGE_VENDOR_FEATURES", os.path.join(gecici, "yok"))
    with pytest.raises(SystemExit, match="no vendor feature XMLs"):
        ap.reconcile_features()


@pytest.mark.parametrize("rel", [DEVICE_MANAGEMENT, FEATURES])
def test_calisma_agaci_guncel(ap, agac, rel):
    gercek = os.path.join(GERCEK_WORK, "sys", rel)
    gerekli(gercek)
    ap.reconcile_features()
    assert oku(gercek) == agac(rel), "çalışma ağacı apply_patches.py'den sonra yeniden yamanmadı"


RO = ("Lcom/android/internal/pm/RoSystemFeatures;", "maybeHasFeature",
      "(Ljava/lang/String;I)Ljava/lang/Boolean;")


def test_derleme_anı_ozellikleri_null_doner(ap, monkeypatch, gecici):
    """framework.jar: maybeHasFeature starts with const/4 v0, #0 ; return-object v0, the dex
    header is valid (dexdump checks it), and every other jar entry is byte-identical."""
    import subprocess
    import zipfile
    gerekli(os.path.join(MICA, "system_a.erofs"), ap.DEXDUMP, ap.ZIPALIGN)
    fw = os.path.join(gecici, "framework")
    os.makedirs(fw)
    monkeypatch.setattr(ap, "WORK", gecici)
    ap.patch_ro_system_features(fw)
    yeni = zipfile.ZipFile(os.path.join(fw, "framework.jar"))
    eski_yol = os.path.join(gecici, "eski.jar")
    yaz(eski_yol, ap.pristine_file("system/framework/framework.jar"))
    eski = zipfile.ZipFile(eski_yol)
    # patch_ro_system_features also carries the camera patch of yamalar/kamera_jpeg.py (classes2.dex
    # rewritten, classes7.dex added; test_kamera_jpeg.py checks those) and the Keystore attestation
    # patch of patch_keystore_attestation (classes3.dex); here only those and the RoSystemFeatures
    # dex may differ
    assert yeni.namelist() == eski.namelist() + ["classes7.dex"]
    farkli = [e for e in eski.namelist() if eski.read(e) != yeni.read(e)]
    assert set(farkli) == {"classes2.dex", "classes3.dex", "classes5.dex"}, farkli
    # verify classes3.dex (AndroidKeyStoreKeyPairGeneratorSpi.addAttestationParameters)
    c3 = os.path.join(gecici, "c3.dex")
    yaz(c3, yeni.read("classes3.dex"))
    r3 = subprocess.run([ap.DEXDUMP, "-d", c3], capture_output=True)
    out3 = r3.stdout.decode(errors="replace")
    assert r3.returncode == 0 and "checksum" not in r3.stderr.decode(errors="replace").lower()
    metot3 = "android.security.keystore2.AndroidKeyStoreKeyPairGeneratorSpi.addAttestationParameters:(Ljava/util/List;)V"
    insns3 = [t for _, t in ap.dex_methods(out3)[metot3]]
    assert not any("isDevicePropertiesAttestationIncluded" in t for t in insns3), insns3[:15]
    assert any("ActivityThread;.currentPackageName:()Ljava/lang/String;" in t for t in insns3), insns3[:15]
    assert any("Build;.TYPE:Ljava/lang/String;" in t for t in insns3), insns3[:15]
    farkli = ["classes5.dex"]
    dex = os.path.join(gecici, "x.dex")
    yaz(dex, yeni.read(farkli[0]))
    r = subprocess.run([ap.DEXDUMP, "-d", dex], capture_output=True)
    out = subprocess.CompletedProcess(r.args, r.returncode, r.stdout.decode(errors="replace"),
                                      r.stderr.decode(errors="replace"))
    assert out.returncode == 0 and "checksum" not in out.stderr.lower(), out.stderr[-500:]
    off, regs, units = ap.dex_method_code(oku(dex, "rb"), *RO)
    kod = out.stdout.split(f"|[{off - 16:06x}] com.android.internal.pm.RoSystemFeatures."
                           f"maybeHasFeature")[1].splitlines()
    assert "|0000: const/4 v0, #int 0" in kod[1] and "|0001: return-object v0" in kod[2], kod[:3]
    # The ART verifier checks every instruction, reachable or not (a leftover half instruction
    # decoded as move/from16 v17 made zygote crash-loop on 22 Oct): the whole body is these
    # two instructions and nops, and the method has no try blocks.
    komutlar = [k.split("|", 1)[1].split(": ", 1)[1].strip() for k in kod[1:units + 1]
                if re.match(r"[0-9a-f]{6}: ", k)]
    assert komutlar[:2] == ["const/4 v0, #int 0 // #0", "return-object v0"], komutlar[:2]
    assert komutlar[2:] and set(komutlar[2:]) == {"nop // spacer"}, sorted(set(komutlar[2:]))[:5]
    assert len(komutlar) == units
    blok = kod[units + 1:units + 6]
    assert any("catches       : (none)" in k for k in blok), blok
    # Patching the patched dex again finds the same method at the same place.
    assert ap.dex_method_code(yeni.read(farkli[0]), *RO) == (off, regs, units)


def test_eski_onyukleme_imaji_kalkar(ap, gecici):
    """Boot image and framework oat artifacts go, nothing else in /system/framework does."""
    fw = os.path.join(gecici, "framework")
    gidecek = ["arm64/boot.art", "arm64/boot-framework.oat", "arm64/boot.art.fsv_meta",
               "boot-framework.vdex", "boot-framework.vdex.fsv_meta",
               "oat/arm64/services.odex", "oat/arm64/am.vdex.fsv_meta",
               "oat/arm64/apex@com.android.appsearch@javalib@service-appsearch.jar@classes.art"]
    kalacak = ["framework.jar", "framework.jar.fsv_meta", "boot-image.bprof", "boot.prof",
               "arm64/README", "bos/dizin.txt"]
    for rel in gidecek + kalacak:
        yaz(os.path.join(fw, rel), "x")
    os.makedirs(os.path.join(fw, "bos2"))
    for ad in ("boot-framework.vdex", "boot-framework.vdex.fsv_meta"):  # as mica ships them
        os.symlink(f"../{ad}", os.path.join(fw, "arm64", ad))
    gidecek += ["arm64/boot-framework.vdex", "arm64/boot-framework.vdex.fsv_meta"]
    ap.invalidate_system_boot_image(fw)
    for rel in gidecek:
        assert not os.path.lexists(os.path.join(fw, rel)), rel
    for rel in kalacak:
        assert os.path.exists(os.path.join(fw, rel)), rel
    assert not os.path.exists(os.path.join(fw, "oat"))
    assert os.path.isdir(os.path.join(fw, "bos2")), "unrelated empty directory removed"


def test_calisma_agacinda_kopuk_bag_yok():
    """No relative symlink under the working tree's /system/framework points at a removed file."""
    fw = os.path.join(GERCEK_WORK, "sys", "system", "framework")
    gerekli(fw)
    kopuk = [os.path.relpath(os.path.join(d, n), fw)
             for d, dirs, files in os.walk(fw) for n in dirs + files
             if os.path.islink(os.path.join(d, n))
             and not os.readlink(os.path.join(d, n)).startswith("/")
             and not os.path.exists(os.path.join(d, n))]
    assert not kopuk, kopuk


@pytest.fixture(scope="module")
def services(ap):
    """Pristine and working-tree services.jar dex files, with their dexdump methods."""
    import zipfile
    gerekli(os.path.join(MICA, "system_a.erofs"), ap.DEXDUMP)
    gercek = os.path.join(GERCEK_WORK, "sys/system/framework/services.jar")
    gerekli(gercek)
    import io
    eski = zipfile.ZipFile(io.BytesIO(ap.pristine_file("system/framework/services.jar")))
    yeni = zipfile.ZipFile(gercek)
    dexler = sorted(n for n in eski.namelist() if re.fullmatch(r"classes\d*\.dex", n))
    return {n: (eski.read(n), yeni.read(n)) for n in dexler}


def _siteler(ap, dex, features):
    return {(m, f, k) for m, f, _, k in ap.feature_check_sites(ap.dex_methods(ap.dexdump(dex)),
                                                                 features)}


def test_derleme_anı_ozellikleri_framework_tablosundan_okunur(ap):
    gerekli(os.path.join(MICA, "system_a.erofs"), ap.DEXDUMP)
    assert ap.compile_time_features() == {
        "android.software.device_admin", "android.hardware.location.gps",
        "android.software.managed_users", "android.hardware.wifi.aware",
        "android.hardware.wifi.rtt"}


def test_services_katlanan_ve_atilan_yerler(ap, services):
    """Pristine services.jar: exactly one folded check (DevicePolicyManagerService's Injector) and
    exactly the dropped checks DROPPED_FEATURE_CHECKS explains. The working tree: no folded check
    left, every dropped one still dropped except where SystemServer was rebuilt."""
    features = ap.compile_time_features()
    once = set().union(*(_siteler(ap, e, features) for e, _ in services.values()))
    sonra = set().union(*(_siteler(ap, y, features) for _, y in services.values()))
    katlanan = {(m, f) for m, f, k in once if k == "folded"}
    assert katlanan == {("com.android.server.devicepolicy.Injector.hasFeature:()Z",
                         "android.software.device_admin")}
    assert {(m, f) for m, f, k in once if k == "dropped"} == set(ap.DROPPED_FEATURE_CHECKS)
    assert not [s for s in sonra if s[2] == "folded"], sonra
    assert ("com.android.server.devicepolicy.Injector.hasFeature:()Z",
            "android.software.device_admin", "used") in sonra
    # rtt and the first aware call were rewritten into the starts; the RangingService call stays
    assert {(m, f) for m, f, k in sonra if k == "dropped"} == {
        (ap.SYSTEM_SERVER_START, "android.hardware.wifi.aware"),
        ("com.android.server.net.NetworkPolicyManagerService.waitForAdminData:()V",
         "android.software.device_admin"),
        ("com.android.server.usage.AppStandbyController.waitForAdminData:()V",
         "android.software.device_admin")}


def test_wifi_aware_ve_rtt_servisleri_baslatilir(ap, services):
    """SystemServer.startOtherServices: right after the WifiUsd block, RttService then
    WifiAwareService are started exactly like WifiService (same registers, same jar)."""
    yeni = next(y for _, y in services.values() if b"startOtherServices" in y and
                ap.SYSTEM_SERVER_START in ap.dex_methods(ap.dexdump(y)))
    t = [x for _, x in ap.dex_methods(ap.dexdump(yeni))[ap.SYSTEM_SERVER_START]]
    def blok(cls):
        i = next(k for k, x in enumerate(t) if x.startswith(f'const-string v3, "{cls}"'))
        return [re.sub(r" // .*", "", x) for x in t[i - 1:i + 3]]
    wifi = blok("com.android.server.wifi.WifiService")
    for cls in ("com.android.server.wifi.rtt.RttService",
                "com.android.server.wifi.aware.WifiAwareService"):
        b = blok(cls)
        assert b[0] == wifi[0] and b[2:] == wifi[2:], (b, wifi)
    i = next(k for k, x in enumerate(t) if '"com.android.server.wifi.rtt.RttService"' in x)
    j = next(k for k, x in enumerate(t) if '"com.android.server.wifi.aware.WifiAwareService"' in x)
    assert j == i + 4, "the two starts are not back to back"
    # and the code after them is untouched: the Wi-Fi Direct check follows
    assert "getPackageManager" in t[j + 3] and '"android.hardware.wifi.direct"' in t[j + 5], t[j:j + 6]


def test_bir_jarin_butun_dex_yamalari_birlikte_kalir(ap, services):
    """services.jar carries the window-manager byte patches (classes3.dex) and the feature fixes
    (other dex files) at once: one rewrite per jar, none overwrites another."""
    degisen = sorted(n for n, (e, y) in services.items() if e != y)
    assert "classes3.dex" in degisen and len(degisen) >= 2, degisen


def test_vendor_bildirmezse_wifi_servisleri_eklenmez(ap):
    gerekli(os.path.join(MICA, "system_a.erofs"), ap.DEXDUMP)
    dex = bytearray(b"degismemeli")
    ap.restore_wifi_aware_rtt(dex, [], {"android.hardware.wifi.rtt"})
    assert dex == bytearray(b"degismemeli")


def test_cdm_guvenli_kanal_debuggable_dalini_atlar(ap):
    """CompanionTransportManager.createTransport: a debuggable build opens a pre-shared-key
    channel with the fixed "CDM" test key while a user build uses attestation, and the two steps
    are exclusive, so this tablet (ro.debuggable=1) and a user-build phone abort each other's
    handshake and no transport is ever built. The patch turns the if-eqz that picks the test key
    into a goto past it, so the attestation path runs while ro.debuggable stays 1 for adb root,
    gbos-guncelleyicid and gbos_linux."""
    import zipfile
    gerekli(os.path.join(MICA, "system_a.erofs"), ap.DEXDUMP)
    eski_dex = zipfile.ZipFile(io.BytesIO(ap.pristine_file("system/framework/services.jar"))
                               ).read("classes.dex")
    # yamayı gerçek tablodan uygula: sabit kopyalanmaz, yamanın kendisi sınanır
    yamalar = ap.jar_byte_patches()[("services.jar", "classes.dex")]
    yeni_dex = ap.fix_dex_header(ap.apply_byte_patches(eski_dex, yamalar, "services.jar!classes.dex"))
    assert len(yeni_dex) == len(eski_dex)
    metot = ("com.android.server.companion.transport.CompanionTransportManager"
             ".createTransport:(Landroid/companion/AssociationInfo;"
             "Landroid/os/ParcelFileDescriptor;[BI)"
             "Lcom/android/server/companion/transport/Transport;")
    eski = [x for _, x in ap.dex_methods(ap.dexdump(eski_dex))[metot]]
    yeni = [x for _, x in ap.dex_methods(ap.dexdump(yeni_dex))[metot]]
    # if-eqz yerine goto + nop: iki kod birimi aynı kalır, ayrıştırıcı nop'u ayrı komut sayar
    assert len(yeni) == len(eski) + 1, (len(eski), len(yeni))
    k = next(i for i, (a, b) in enumerate(zip(eski, yeni)) if a != b)
    assert "if-eqz" in eski[k] and "goto" in yeni[k], (eski[k], yeni[k])
    # aynı hedef: test anahtarı dalının ötesindeki PSK/attestation yolu
    hedef = re.search(r"// \+([0-9a-f]+)$", eski[k].rstrip())
    assert hedef and yeni[k].rstrip().endswith(hedef.group(1)), (eski[k], yeni[k])
    assert "0048" in yeni[k], yeni[k]
    assert yeni[k + 1] == "nop // spacer", yeni[k:k + 2]
    # kaydırma dışında hiçbir komut değişmemiş: nop'tan sonrası birebir aynı
    assert eski[k + 1:] == yeni[k + 2:], (eski[k + 1:k + 4], yeni[k + 2:k + 5])
    # dalın kendisi dexte duruyor, yalnız bu metottan ulaşılamıyor
    assert any('const-string v0, "CDM"' in x for x in yeni), yeni
    assert any("Build;.isDebuggable:()Z" in x for x in yeni), yeni


def test_stok_googlebook_kimligi_ve_laptop_controller_korunur(ap, monkeypatch, gecici):
    """Stock Googlebook OS (mica) identity (Dell / mica / XPS Googlebook from /product/etc/build.prop)
    and DisplayRotation$LaptopController (SLATE auto-rotates, LID_OPEN locks landscape) are kept as
    pristine mica ships them: no ro.product.property_source_order in /product/etc/build.prop, and
    LaptopController.refreshConfig() in services.jar!classes3.dex is unpatched."""
    import zipfile
    gerekli(os.path.join(MICA, "system_a.erofs"), os.path.join(MICA, "product_a.erofs"), ap.DEXDUMP)
    eski_dex = zipfile.ZipFile(io.BytesIO(ap.pristine_file("system/framework/services.jar"))
                               ).read("classes3.dex")
    yamalar = ap.jar_byte_patches()[("services.jar", "classes3.dex")]
    yeni_dex = ap.fix_dex_header(ap.apply_byte_patches(eski_dex, yamalar, "services.jar!classes3.dex"))
    metot = "com.android.server.wm.DisplayRotation$LaptopController.refreshConfig:()V"
    eski = [x for _, x in ap.dex_methods(ap.dexdump(eski_dex))[metot]]
    yeni = [x for _, x in ap.dex_methods(ap.dexdump(yeni_dex))[metot]]
    assert yeni == eski, (eski, yeni)
    gercek_prod_bp = os.path.join(GERCEK_WORK, "prod/etc/build.prop")
    gerekli(gercek_prod_bp)
    assert "ro.product.property_source_order=" not in oku(gercek_prod_bp)

