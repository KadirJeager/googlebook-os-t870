# Installing Googlebook OS on a Galaxy Tab S7 (SM-T870)

You build the system on a Linux computer from Google's Googlebook OS release and your own
LineageOS build, then write it to the tablet from LineageOS recovery. Read the whole guide
first. Installing erases the tablet's data.

Status: on 31 October 2026 this guide was followed end to end from a fresh clone, with new
keys and Google's then current release (.276), on a tablet wiped back to LineageOS 24.0: it
booted Googlebook OS and passed the acceptance checks (`cihaz-kabul/kabul.sh`, no failures).
Section 2 (stock firmware to LineageOS) is LineageOS's own procedure. Report what you find.

## 1. What you need

- A Galaxy Tab S7 **Wi-Fi, SM-T870** (`gts7lwifi`). Not the LTE (SM-T875) or S7+ models.
- A Linux x86_64 computer with 16 GB of RAM and about 30 GB free under `/var/tmp` (the work
  directory: 12 GB; kernel sources and compiler: 4 GB; the Googlebook OS partitions: 4 GB;
  images). `GBOS_WORK` and `GBOS_KERNEL_IS` move the work directories elsewhere.
- On that computer:
  - `git`, `curl`, `python3` (3.11 or newer), `unzip`, `patch`, a C/C++ compiler, `brotli`,
    `zstd`
  - a JDK 21 (`javac`, `jar`)
  - Android SDK (`ANDROID_HOME`, default `~/Android/Sdk`) with build-tools `37.0.0` and
    `35.0.0`, platforms `android-37.0` and `android-37.2`, and NDK `30.0.16248370`
    (`sdkmanager "build-tools;37.0.0" "build-tools;35.0.0" "platforms;android-37.0"
    "platforms;android-37.2" "ndk;30.0.16248370"`)
  - android-tools 37 (`adb`, `avbtool`, `mkbootimg`, `unpack_bootimg`, `lpdump`,
    `e2fsdroid`) and e2fsprogs (`mke2fs`, `e2fsck`, `debugfs`)
  - erofs-utils 1.9 or newer (`fsck.erofs`, `dump.erofs`); `EROFS_BIN` names their directory
    (default `~/.local/bin`)

  Every script checks its tools first and names the one that is missing.

## 2. LineageOS 24.0 on the tablet

Install LineageOS 24.0 for gts7lwifi exactly as LineageOS describes it:
<https://wiki.lineageos.org/devices/gts7lwifi/install/> (stock Android 13 firmware first,
bootloader unlock, Lineage Recovery, then the LineageOS zip). Keep the zip you installed
(`lineage-24.0-<date>-nightly-gts7lwifi-signed.zip` from
<https://download.lineageos.org/devices/gts7lwifi/builds>): Googlebook OS uses that build's
kernel, vendor and odm, and the tools read them from the zip. No Google apps package is needed.

Boot LineageOS once, enable Developer options, USB debugging and "Rooted debugging".

## 3. Build

```sh
git clone https://github.com/KadirJeager/googlebook-os-t870.git GooglebookOS/T870-araclar
cd GooglebookOS/T870-araclar
mkdir -p LineageOS-24.0-gts7lwifi
cp ~/Downloads/lineage-24.0-*-gts7lwifi-signed.zip LineageOS-24.0-gts7lwifi/
```

1. Your signing keys (kept in `~/.android-certs/googlebookos-t870`, never published; keep a
   backup: updates must be signed with the same keys):

   ```sh
   ./anahtar-olustur.sh
   ```

2. Googlebook OS from Google (only the needed partitions, about 4 GB):

   ```sh
   python3 mica-indir.py
   ```

3. The LineageOS inputs, from your zip:

   ```sh
   python3 lineage-hazirla.py
   ```

4. The tablet's super partition header. Boot the tablet into LineageOS recovery (power off,
   then Volume Up + Power), choose Advanced > Enable ADB, connect it, then:

   ```sh
   python3 super-baslik/uret.py yakala
   ```

   This records your LineageOS layout; Googlebook OS gets the space after it, and LineageOS's
   own partitions stay untouched (the way back, section 6).

5. The kernel (LineageOS's kernel rebuilt from source with the cover patches; downloads the
   kernel source and LineageOS's compiler, about 4 GB):

   ```sh
   kernel/build.sh kaynak
   kernel/build.sh derle yamali      # GBOS_KERNEL_JOBS=4 if memory is short
   kernel/build.sh boot yamali
   ```

6. LineageOS recovery that also accepts the updater's packages:

   ```sh
   python3 recovery-anahtar-ekle.py
   ```

7. The system images. The chain downloads the face unlock sources and the Terminal's
   third-party files on its first run, patches, checks and writes
   `$GBOS_WORK/denetim/imaj/{system,system_ext,product}.img`:

   ```sh
   GBOS_DEX_DOGRULA=atla ./imaj-uret.sh
   ```

   Without `GBOS_DEX_DOGRULA=atla` the patched framework is also checked by the tablet's own
   ART verifier; the tablet then has to be booted into Android (LineageOS) with rooted ADB.

## 4. Install

Boot the tablet into LineageOS recovery with ADB enabled (as in step 3.4), then:

```sh
python3 ilk-kurulum.py
```

It writes Googlebook OS's system, system_ext and product into super (with the Googlebook OS
header), then the patched boot and the recovery, and reads each back. Then, on the tablet:
**Factory reset > Format data**, and **Reboot system now**. The first boot takes a few minutes.

Optional: Google's apps keep some Googlebook features (Quick Look, the ALAI/Loam smart
collections, the Gemini/Search/ASI shortcuts, Pixel live wallpapers) off by Google's server
flags. `telefon-merkezi/bayrak/fmd-bayraklari.sh` overrides those flags over rooted ADB once
you are signed in and online (`telefon-merkezi/bayrak/BENIOKU.md`).

## 5. Updates

Settings > System > System update. The tablet finds a new Googlebook OS release itself,
prepares the images in its Linux environment and installs them overnight while charging; a
release that does not boot is rolled back on its own. Details: `guncelleyici/BENIOKU.md`.

## 6. Back to LineageOS

LineageOS's system, system_ext and product are still in super. Write LineageOS's own boot and
recovery back (from your zip: `python3 lineage-zip.py cikar boot.img boot.img`, the same for
`recovery.img`) and the recorded LineageOS super header (`super-baslik/lineage.bin.xz`,
decompressed, to the first MiB of `/dev/block/by-name/super`), from recovery over ADB, then
Format data. Or simply install LineageOS again from its zip as in section 2.
