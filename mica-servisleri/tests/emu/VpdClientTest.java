// Client side of the emulator test (tests/emu/emu-run.sh), run with app_process.
//
// It reaches the service the way com.android.desktop.serviceability does
// (ServiceManager.waitForService + the AIDL Java proxy generated from the same frozen interface)
// and checks what that service, Setup Wizard's activate date and My Googlebook's
// ubind_attribute lookup rely on.
//
//   VpdClientTest first   fresh store: interface, RO, RW writes, errors
//   VpdClientTest again   after the service exited and was started again: RW persisted
//   VpdClientTest denied  as an app uid: refused
import android.os.IBinder;
import android.os.ServiceManager;
import android.os.ServiceSpecificException;
import android.os.SystemClock;
import android.os.SystemProperties;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import vendor.google.desktop.vpd_executor.IVpdExecutor;
import vendor.google.desktop.vpd_executor.VpdEntry;

public class VpdClientTest {
    private static final String NAME = "vendor.google.desktop.vpd_executor.IVpdExecutor/default";
    private static final String HASH = "11049698b211cb439aad0e72f4c391331ed7063b";
    private static final byte RO = 0;
    private static final byte RW = 1;
    private static int sFailures = 0;

    private static void check(boolean ok, String what) {
        System.out.println((ok ? "PASS " : "FAIL ") + what);
        if (!ok) sFailures++;
    }

    private static VpdEntry entry(String key, String value) {
        VpdEntry e = new VpdEntry();
        e.key = key;
        e.value = value;
        return e;
    }

    private static String firstProperty(String... names) {
        for (String n : names) {
            String v = SystemProperties.get(n, "").trim();
            if (!v.isEmpty() && !v.equals("unknown")) return v;
        }
        return null;
    }

    private interface Call {
        void run() throws Exception;
    }

    private static void expectServiceError(Call c, String what) {
        try {
            c.run();
            check(false, what + ": no exception");
        } catch (ServiceSpecificException e) {
            check(e.errorCode == -1, what + ": ServiceSpecificException(" + e.errorCode + ", "
                    + e.getMessage() + ")");
        } catch (Exception e) {
            check(false, what + ": " + e);
        }
    }

    private static void expect(Class<? extends Exception> type, Call c, String what) {
        try {
            c.run();
            check(false, what + ": no exception");
        } catch (Exception e) {
            check(type.isInstance(e), what + ": " + e);
        }
    }

    public static void main(String[] args) throws Exception {
        String mode = args.length > 0 ? args[0] : "first";
        long start = SystemClock.uptimeMillis();
        IBinder binder = ServiceManager.waitForService(NAME);
        long waited = SystemClock.uptimeMillis() - start;
        check(binder != null, "waitForService(" + NAME + ") returned after " + waited + " ms");
        check(waited < 5000, "lazy start took less than 5 s");
        IVpdExecutor vpd = IVpdExecutor.Stub.asInterface(binder);

        if (mode.equals("denied")) {
            expect(SecurityException.class, () -> vpd.getEntries(RO), "app uid refused");
            System.exit(sFailures == 0 ? 0 : 1);
        }

        String serial = firstProperty("ro.boot.serialno", "ro.serialno");
        String model = firstProperty("ro.boot.em.model", "ro.product.vendor.model");

        if (mode.equals("first")) {
            check(vpd.getInterfaceVersion() == 1, "interface version 1");
            check(HASH.equals(vpd.getInterfaceHash()), "interface hash " + HASH);

            List<VpdEntry> ro = vpd.getEntries(RO);
            List<String> expectedKeys = new ArrayList<>();
            if (serial != null) expectedKeys.add("serial_number");
            if (model != null) expectedKeys.add("model_name");
            List<String> keys = new ArrayList<>();
            for (VpdEntry e : ro) keys.add(e.key);
            check(keys.equals(expectedKeys), "RO keys " + keys + " == " + expectedKeys);
            check(serial == null
                    || serial.equals(vpd.getValue(RO, "serial_number").value),
                    "RO serial_number is the device serial " + serial);
            check(model == null || model.equals(vpd.getValue(RO, "model_name").value),
                    "RO model_name is the device model " + model);

            // My Googlebook: ServiceabilityService.getUbindAttribute().
            VpdEntry ubind = vpd.getValue(RO, "ubind_attribute");
            check(ubind != null && "ubind_attribute".equals(ubind.key) && ubind.value == null,
                    "missing RO key: entry with the key and a null value");
            for (String k : Arrays.asList("mlb_serial_number", "imei", "meid", "mfg_sku_id",
                    "region", "oem_key2", "kb_info")) {
                // ServiceabilityService.getVpdStringValue(): getValue(...).value
                check(vpd.getValue(RO, k).value == null, "RO " + k + " absent");
            }

            // Setup Wizard: ServiceabilityService.writeActivateDate().
            check(vpd.getValue(RW, "activate_date").value == null, "fresh RW has no activate_date");
            check(vpd.getEntries(RW).isEmpty(), "fresh RW is empty");
            vpd.updateEntries(RW, Arrays.asList(entry("activate_date", "2026-43")));
            check("2026-43".equals(vpd.getValue(RW, "activate_date").value),
                    "activate_date written and read back");
            vpd.updateEntries(RW, Arrays.asList(entry("tmp_key", "x"), entry("tmp_key", null)));
            check(vpd.getValue(RW, "tmp_key").value == null, "null value deletes the key");
            vpd.updateEntries(RW, Arrays.asList(entry("never_set", null)));
            check(vpd.getEntries(RW).size() == 1, "deleting a missing key is a no-op");

            expectServiceError(() -> vpd.updateEntries(RO, Arrays.asList(entry("a", "b"))),
                    "RO_VPD write refused");
            expectServiceError(() -> vpd.updateEntries(RW,
                    Arrays.asList(entry("big", new String(new char[40000]).replace('\0', 'v')))),
                    "RW_VPD over 32 KiB refused");
            expect(IllegalArgumentException.class,
                    () -> vpd.updateEntries(RW, Arrays.asList(entry("ok", "1"), entry("a=b", "2"))),
                    "invalid key refused");
            check(vpd.getValue(RW, "ok").value == null, "refused update changed nothing");
            expect(IllegalArgumentException.class,
                    () -> vpd.updateEntries(RW, Arrays.asList(entry(null, "x"))), "null key refused");
            expect(IllegalArgumentException.class, () -> vpd.getEntries((byte) 2),
                    "invalid region refused");
        } else if (mode.equals("again")) {
            List<VpdEntry> rw = vpd.getEntries(RW);
            check(rw.size() == 1 && "activate_date".equals(rw.get(0).key)
                    && "2026-43".equals(rw.get(0).value), "RW survived the service restart");
        } else {
            throw new IllegalArgumentException(mode);
        }
        System.exit(sFailures == 0 ? 0 : 1);
    }
}
