/*
 * Frozen version 1 of vendor.google.desktop.vpd_executor.IVpdExecutor as shipped by mica
 * (Dell XPS Googlebook) in vendor/bin/hw/vendor.google.desktop.vpd_executor.vpd-executor,
 * reconstructed from its callers: com.android.desktop.serviceability and desktop-services.jar
 * (IVpdExecutor.VERSION = 1, IVpdExecutor.HASH = 11049698b211cb439aad0e72f4c391331ed7063b).
 * Transaction codes follow declaration order: getEntries = 1, getValue = 2, updateEntries = 3.
 */
package vendor.google.desktop.vpd_executor;

import vendor.google.desktop.vpd_executor.VpdEntry;

@VintfStability
interface IVpdExecutor {
    /** All entries of the region (VpdRegion), in storage order. */
    List<VpdEntry> getEntries(in byte region);

    /** The entry for key; its value is null when the region has no such key. */
    @nullable VpdEntry getValue(in byte region, in String key);

    /** Sets each entry; an entry with a null value deletes its key. */
    void updateEntries(in byte region, in List<VpdEntry> entries);
}
