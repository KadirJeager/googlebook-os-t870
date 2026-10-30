// RW_VPD of the tablet: a key/value store kept in one file under /data, replaced atomically.
//
// A Googlebook keeps RW_VPD in the firmware flash and the vpd tool rewrites it in place. The
// tablet has no such region, so the entries live in <dir>/rw_vpd. Every update writes the whole
// store to <dir>/rw_vpd.tmp, fsyncs it, renames it over rw_vpd and fsyncs the directory, so a
// crash or power loss leaves either the old or the new store, never a mix. The file carries a
// magic, a version and a CRC32; a file that fails these checks is moved aside to
// rw_vpd.corrupt (kept for inspection) and the store starts empty, which is reported in logcat.
//
// File layout (integers little endian):
//   "GBOSVPD\n"  u32 version (1)  u32 count
//   count x { u32 key_len, key bytes, u32 value_len, value bytes }
//   u32 crc32 (IEEE 802.3) of everything before it
#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace gbos::vpd {

struct Entry {
    std::string key;
    std::string value;
};

// One requested change: a value sets the key, no value deletes it.
struct Update {
    std::string key;
    std::optional<std::string> value;
};

enum class Status {
    kOk,
    kInvalidArgument,  // a key or value the store does not accept; nothing was changed
    kNoSpace,          // the result would exceed kMaxPayloadBytes; nothing was changed
    kIoError,          // the store could not be read or written; nothing was changed
};

struct Result {
    Status status = Status::kOk;
    std::string message;
    bool ok() const { return status == Status::kOk; }
};

// Keys are what the vpd tool can store and list unambiguously: 1..kMaxKeyBytes printable ASCII
// characters other than '"' (the list output quotes keys) and '=' (key=value on its command line).
constexpr size_t kMaxKeyBytes = 128;
// Values are UTF-8 text without NUL bytes (they are passed as command line arguments there).
// The firmware layouts of Qualcomm Chromebooks reserve 32 KiB for RW_VPD (coreboot
// src/mainboard/google/{trogdor,herobrine}/chromeos.fmd: "RW_VPD(PRESERVE) 32K"); the store keeps
// the same bound on the encoded entries (4 + key + 4 + value bytes each).
constexpr size_t kMaxPayloadBytes = 32 * 1024;

bool IsValidKey(const std::string& key);
bool IsValidValue(const std::string& value);
uint32_t Crc32(const uint8_t* data, size_t size);

class RwStore {
  public:
    explicit RwStore(std::string dir);

    // Reads the store from disk. A missing file is an empty store. A corrupt file is moved to
    // rw_vpd.corrupt and the store is empty. Returns kIoError when the directory or file cannot
    // be read; the store then refuses every operation until a later Load() succeeds.
    Result Load();
    bool loaded() const { return loaded_; }

    const std::vector<Entry>& entries() const { return entries_; }
    std::optional<std::string> Get(const std::string& key) const;

    // Applies all updates in order, all or nothing: validates them, writes the new store
    // atomically and only then replaces the in-memory entries. Deleting a missing key is a no-op.
    // An existing key keeps its position; a new key is appended.
    Result Apply(const std::vector<Update>& updates);

    std::string path() const { return dir_ + "/" + kFileName; }

    static constexpr const char* kFileName = "rw_vpd";
    static constexpr const char* kTempName = "rw_vpd.tmp";
    static constexpr const char* kCorruptName = "rw_vpd.corrupt";

    // Encoding, exposed for the tests.
    static std::vector<uint8_t> Encode(const std::vector<Entry>& entries);
    static std::optional<std::vector<Entry>> Decode(const std::vector<uint8_t>& data,
                                                    std::string* why);

  private:
    // Sets *replaced once rw_vpd has been renamed into place.
    Result Write(const std::vector<Entry>& entries, bool* replaced);

    std::string dir_;
    bool loaded_ = false;
    std::vector<Entry> entries_;
};

size_t PayloadBytes(const std::vector<Entry>& entries);

}  // namespace gbos::vpd
