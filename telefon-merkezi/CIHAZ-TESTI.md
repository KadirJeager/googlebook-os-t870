# Cihazda doğrulama: Telefon Merkezi senkronu (DND, görev sürekliliği, dosya paylaşımı)

Pixel 10 Pro ile Galaxy Tab S7 (Googlebook OS). Testler ana oturumda, iki cihaz aynı Wi-Fi'da
(Ev_MLO) ve yakınken koşulur. Tablet: `A="adb"`, `$A root`. Telefon:
`P="adb -s <telefonun seri numarası>"`; **telefonda yalnız okuma, günlük silinmez** (DEVIR kuralı).

Bu modülde imaj yaması YOK. Kök neden: Android 17'nin CDM senkronu taşıma, tarama ve yayını
ilişkilendirmenin sahibi Cross-device Services'a `onActionRequested` ile yaptırır; uygulamanın 1346
sürümü bu yöntemi uygulamıyor, istekler 2 sn'de zaman aşımına düşüyor. Ayrıntı ve kanıt:
`acilis-loglari/telefon-merkezi-20261023-senkron/KOK-NEDEN.md`. DND ve görev sürekliliği,
Cross-device Services bu yöntemi uygulayan bir sürüme güncellenene dek **başarısız** beklenir.
Her kabulde telefonda şu satırlara bakılır: `logcat -d -b all | grep CDM_ActionRequestProcessor`;
`Processing action result` görünürse uygulama güncellenmiştir ve testler yeniden koşulur.

## 0. Kök nedenin cihazda kanıtı (önce bu)

Taşıma kanalının neden kurulmadığını doğrudan gösterir. Beklenen çıktı, sorun sürdüğünü teyit eder.

```bash
# 1) Yerel sistem-verisi taşıması iki cihazda da boş:
$A shell dumpsys companiondevice | grep -E "System Data Transports|associationsWithTransport"
$P shell dumpsys companiondevice | grep -E "System Data Transports|associationsWithTransport"
#    Beklenen: "System Data Transports: <empty>" ve "associationsWithTransport=[]" (iki cihaz).

# 2) DND üretiliyor ama gönderilecek eş yok:
$A shell dumpsys companiondevice | grep -E "hasSyncableRemoteDevice|MANUAL_DO_NOT_DISTURB|contextual_mode_sync"
$P shell dumpsys companiondevice | grep -E "hasSyncableRemoteDevice|MANUAL_DO_NOT_DISTURB|contextual_mode_sync"
#    Beklenen: "hasSyncableRemoteDevice=false"; modes cache'inde MANUAL_DO_NOT_DISTURB var;
#    contextual_mode_sync ağında devices=[] (teslim hedefi yok).

# 3) ASIL KANIT — _FC9F5ED42C8A._tcp için keşif istemcisi yok, yalnız reklam var:
$A shell dumpsys servicediscovery | grep -E "_FC9F5ED42C8A._tcp"
$P shell dumpsys servicediscovery | grep -E "_FC9F5ED42C8A._tcp"
#    Beklenen: her iki cihazda yalnız "Advertiser: ..._FC9F5ED42C8A._tcp" satır(lar)ı.
#    "Discovery/DiscoveryListener: serviceType=_FC9F5ED42C8A._tcp" ya da bu türde bir
#    ResolutionListener / SERVICE_FOUND SATIRI OLMAMALI. İki cihaz birbirini bu hizmette hiç
#    keşfetmediği için 53601 soketi hiç kurulmaz → CDM taşıması hiç eklenmez → DND/görev sürmez.
```

Ağın sağlam olduğunu teyit (keşif eksikliğinin ağ sorunu olmadığını gösterir):

```bash
$A shell ip addr | grep "192.168.0"; $P shell ip addr | grep "192.168.0"   # ikisi de Ev_MLO'da
# Bilgisayardan: avahi-browse -rt _FC9F5ED42C8A._tcp   # iki cihaz da 53601'de görünür
# Port: nc -vz 192.168.0.206 53601 ve nc -vz 192.168.0.250 53601   # iki yön de açık
```

## 1. Rahatsız Etmeyin (DND) — iki yön

```bash
# Başlangıç
$A shell settings get global zen_mode; $P shell settings get global zen_mode   # ikisi 0

# Telefon → tablet
$P shell cmd notification set_dnd priority
for i in $(seq 1 30); do $A shell settings get global zen_mode; sleep 1; done   # 0 kalıyorsa gelmedi
$P shell cmd notification set_dnd off

# Tablet → telefon
$A shell cmd notification set_dnd priority
for i in $(seq 1 30); do $P shell settings get global zen_mode; sleep 1; done
$A shell cmd notification set_dnd off
```

Kabul (gerçek kullanım): telefonda Hızlı Ayarlar'dan **elle** DND açılınca ~5 s içinde tablette
zen_mode != 0 olmalı ve bildirim gölgesinde DND rozeti görünmeli; tersi de. Mevcut durum:
**başarısız** (§ 0 kapısı). Geçerse kök nedenin değiştiği anlamına gelir, § 0 yeniden koşulur.

## 2. Görev sürekliliği (task handoff)

```bash
$P shell am start -a android.intent.action.VIEW -d https://example.com
# Tablette görev çubuğu/son uygulamalarda "telefondan devam et" ögesi beklenir.
$A shell dumpsys companiondevice | grep -E "task_continuity|TASK|Messenger" 
```

Kabul: telefonda açılan sayfa/uygulama için tablette "devam et" ögesi görünmeli ve dokununca
açılmalı. Mevcut durum: **başarısız** (aynı taşıma kanalı; § 0). Chrome hesap senkronuna bağlı
kısım bundan ayrıdır, karıştırılmamalı.

## 3. Dosya ve medya paylaşımı

Telefon → tablet bir dosya paylaş (Paylaş → cihaz adı). Kabul: dosya tablette alınır. Mevcut durum:
Quick Share (GMS Nearby) ayrı bir yoldur; bu modülün taşıma kanalından bağımsız sınanır. Sonuç
ölçülüp kaydedilmeli (bu oturumda ölçülmedi).

## 4. İlişkilendirme sayısı artmamalı (hijyen regresyonu)

ambient.streaming var olan ilişkilendirmeyi kalıcı olmayan süreç-içi haritayla aradığından, süreç
her yeniden başladığında yeni bir self-managed kayıt açar (tablette 13 birikti). Bu, DND sorununu
açıklamaz ve çözmez; yalnız hijyen.

```bash
# Sayı
$A shell cmd companiondevice list 10 | grep -c "com.google.ambient.streaming"
# Güvenli temizlik planı (hesaba/saate/kulaklığa/App Cast kaydına dokunmaz; eş başına en yeniyi tutar)
$A shell dumpsys companiondevice > /tmp/t.txt
python3 -I telefon-merkezi/ilishkilendirme_temizle.py --uygula /tmp/t.txt   # komutları YALNIZ yazdırır
# Üretilen 'cmd companiondevice disassociate-all 10' komutunu ana oturumda $A shell ile çalıştır; uygulama birkaç saniyede tek yeni kayıt açar.
# Sonra tekrar say: tek kayıt kalmalı.
$A shell cmd companiondevice list 10 | grep -c "com.google.ambient.streaming"
```

Kabul: temizlik sonrası ambient.streaming için eş başına tek ilişkilendirme kalır; saat/kulaklık ve
hesap/App Cast kayıtları olduğu gibi durur. Bir gün normal kullanımdan sonra sayının tekrar
bağlantı başına birer birer artıp artmadığı ölçülüp kaydedilmeli (uygulama hatası, Play
güncellemesiyle düzelebilir; imaj tarafında meşru düzeltme yok).

## 5. Bildirim senkronu (gerilememeli)

```bash
$P shell cmd notification post -t "test" tag1 "gövde"
$A shell dumpsys notification | grep -A3 "multidevice_sync"   # ~5 s içinde görünmeli
```

Kabul: bildirim ~5 s içinde tablette `multidevice_sync` grubunda görünür. Bu **buluttan** gelir,
§ 0 taşıma kanalından bağımsızdır; bu yüzden DND başarısızken bile çalışır ve çalışmaya devam
etmelidir.
