import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;

import java.io.FileOutputStream;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.cert.Certificate;
import java.security.spec.ECGenParameterSpec;

/**
 * Generates an EC key in AndroidKeyStore with an attestation challenge, the way the Telefon
 * Merkezi (com.google.ambient.streaming) DeviceLink handshake does, writes the certificate chain
 * (DER, leaf first) to the given directory and deletes the key again. The leaf certificate's
 * KeyDescription extension (1.3.6.1.4.1.11129.2.1.17) carries the RootOfTrust the peer checks.
 *
 *   app_process /system/bin AnahtarOnayi <directory>
 */
public class AnahtarOnayi {
    public static void main(String[] args) throws Exception {
        // app processes install the provider at startup; a bare app_process does not
        Class.forName("android.security.keystore2.AndroidKeyStoreProvider").getMethod("install").invoke(null);
        String alias = "gbos-anahtar-onayi";
        KeyPairGenerator g = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore");
        g.initialize(new KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setAttestationChallenge("gbos-kabul".getBytes())
                .setDevicePropertiesAttestationIncluded(true)
                .build());
        g.generateKeyPair();
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        Certificate[] chain = ks.getCertificateChain(alias);
        for (int i = 0; i < chain.length; i++) {
            try (FileOutputStream o = new FileOutputStream(args[0] + "/sertifika" + i + ".der")) {
                o.write(chain[i].getEncoded());
            }
        }
        ks.deleteEntry(alias);
        System.out.println("zincir=" + chain.length);
    }
}
