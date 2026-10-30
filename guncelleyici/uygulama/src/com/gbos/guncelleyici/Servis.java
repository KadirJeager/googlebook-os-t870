package com.gbos.guncelleyici;

import android.net.LocalSocket;
import android.net.LocalSocketAddress;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

/** Client of the root service gbos-guncelleyicid (/dev/socket/gbos_guncelleyici). */
final class Servis {
    private static final String SOKET = "/dev/socket/gbos_guncelleyici";
    private static final int ZAMAN_ASIMI_MS = 30_000;

    private Servis() {}

    /** Sends one request line and returns the whole reply. Blocking: never on the main thread. */
    static String iste(String istek) throws IOException {
        try (LocalSocket s = new LocalSocket()) {
            s.connect(new LocalSocketAddress(SOKET, LocalSocketAddress.Namespace.FILESYSTEM));
            s.setSoTimeout(ZAMAN_ASIMI_MS);
            OutputStream out = s.getOutputStream();
            out.write((istek + "\n").getBytes(StandardCharsets.UTF_8));
            out.flush();
            InputStream in = s.getInputStream();
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] b = new byte[4096];
            int n;
            while ((n = in.read(b)) > 0) buf.write(b, 0, n);
            return buf.toString(StandardCharsets.UTF_8);
        }
    }

    static Durum durum() throws IOException {
        return Durum.ayristir(iste("durum"));
    }

    /** Starts hazirla, iptal or kur; the service answers "basladi" or "hata: ...". */
    static void baslat(String islem) throws IOException {
        String yanit = iste(islem).trim();
        if (!yanit.equals("basladi")) throw new IOException(yanit);
    }
}
