# Googlebook OS for the Galaxy Tab S7 (SM-T870)

Tools that turn Google's Googlebook OS (Android 17 desktop, the image of the Dell XPS Googlebook,
codename `mica`) into a system for the Wi-Fi Galaxy Tab S7 (SM-T870, `gts7lwifi`), on top of
LineageOS 24.0's kernel, vendor and odm.

Nothing of Google's, Samsung's or LineageOS's is in this repository. You download the Googlebook
OS release from Google (`mica-indir.py`) and the LineageOS build from LineageOS yourself; the
tools patch and repackage them on your computer, and you install the result on your own tablet.

**Install:** [INSTALL.md](INSTALL.md).

## What works

Measured on the author's tablet with `cihaz-kabul/kabul.sh` (42 checks) and by hand:

- Desktop and tablet modes. The Book Cover Keyboard (EF-DT870) and its touchpad; folding the
  cover back or detaching it switches to tablet mode (kernel patches, `kernel/`), and a
  "Tablet mode" Quick Settings tile covers the "inverted L" pose the hardware cannot detect.
- Display in its natural landscape orientation, touch, S Pen, auto-rotation, Vulkan rendering.
- Wi-Fi, Bluetooth, audio, front and rear cameras (photo and video, Google Camera and other
  apps), hardware video codecs.
- Fingerprint (side key) and face unlock (FaceSense, built from source: `yuz-tanima/`).
- Battery health and charging information (`pil-sagligi/`), device information services the
  Googlebook system expects (`mica-servisleri/`).
- Google Play, Gemini, Circle to Search and the other Googlebook apps.
- Phone Hub with a Pixel phone (notifications, Do Not Disturb, file sharing, app streaming,
  Find Hub) works only when the phone is rooted and runs a hook that accepts this tablet's
  attestation; that hook is not part of this repository. Without it the tablet works
  normally, without Phone Hub.
- The Googlebook Linux environment (Terminal) on the 4.19 kernel: a Debian container without
  the virtualisation the original uses (`terminal/`).
- SELinux enforcing.
- Updates: the tablet downloads a new Googlebook OS release itself, patches it on the device
  and installs it; if the new system does not come up, it returns to the previous one
  (`guncelleyici/`).

Known limits: the Find Hub icon shows a generic phone; Create My Widget's server is region
locked; animated WhatsApp wallpapers do not play.

## Layout

| Path | What |
|---|---|
| `apply_patches.py`, `yamalar/` | Patches mica's partitions into the tablet's system, system_ext and product |
| `yama-dogrula.py`, `testler/` | Checks: the tree equals "pristine + patches", regression tests |
| `imaj-uret.sh`, `make_ext4_images.py` | The whole chain, and the ext4 images |
| `lineage-hazirla.py`, `lineage-zip.py` | Inputs taken from your LineageOS zip |
| `mica-indir.py` | Downloads only the needed partitions of Google's recovery image |
| `super-baslik/`, `tablete-yaz.py`, `ilk-kurulum.py` | Super partition layout and installation |
| `kernel/` | LineageOS's kernel rebuilt bit for bit, plus the cover patches |
| `recovery-anahtar-ekle.py` | LineageOS recovery that also accepts the updater's packages |
| `guncelleyici/` | The on-device updater (app, engine, recovery package, kit) |
| `dogal-yon/`, `kamera-jpeg/`, `pil-sagligi/`, `mica-servisleri/` | HALs and framework fixes for this hardware |
| `terminal/`, `tablet-modu/`, `yuz-tanima/` | Linux environment, tablet mode tile, face unlock |
| `selinux/` | Policy additions and the denial replay that checks them |
| `cihaz-kabul/` | Acceptance run on the tablet |

Most documentation inside the directories (`BENIOKU.md`, `CIHAZ-TESTI.md`) is in Turkish; code
comments and this file are in English.

## Licences

This project is licensed under the GNU General Public License v3.0 ([LICENSE](LICENSE)).
The kernel patches (`kernel/kaynak-yamalari/`) are GPL-2.0, like the kernel they patch. Third-
party sources keep their own licences, next to them:

- `terminal/third_party/termux/`: Termux terminal-emulator and terminal-view, Apache-2.0
- `terminal/third_party/nlohmann/`: JSON for Modern C++, MIT
- `terminal/third_party/systemctl/`: docker-systemctl-replacement, EUPL-1.2
- `mica-servisleri/third_party/libbinder_ndk/`, `yuz-tanima/aosp/`: AOSP, Apache-2.0
- `yuz-tanima/yamalar/`: patches to Evolution X's FaceUnlock (Apache-2.0), downloaded by
  `yuz-tanima/getir.sh` together with its Megvii libraries, which are not redistributed here
- `guncelleyici/kurtarma/avb/testkey_rsa4096.pem`: AOSP's public AVB test key, with which
  LineageOS signs its recovery

Downloaded at build time, pinned by version and SHA-256 (`terminal/third_party/fetch.sh`):
androidx.annotation (Apache-2.0) and Debian's bindfs packages (GPL-2.0+).

Googlebook OS, Android and Google apps are Google's; this project is not affiliated with Google,
Dell, Samsung or LineageOS.
