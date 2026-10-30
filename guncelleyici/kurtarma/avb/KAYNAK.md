`testkey_rsa4096.pem` is AOSP's public AVB test key
(https://android.googlesource.com/platform/external/avb/+/refs/heads/main/test/data/testkey_rsa4096.pem).
LineageOS signed the tablet's recovery footer with it: public key SHA-1 2597c218aae470a130f61162feaae70afd97f011,
the same value `avbtool info_image` shows for yedek-20261020/recovery.img. recovery-paketle.py uses it to put the
footer back unchanged in form. vbmeta has flags=3, so the bootloader does not check this footer.
