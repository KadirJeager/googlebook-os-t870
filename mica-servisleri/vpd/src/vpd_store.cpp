#include "vpd_store.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <array>
#include <cstring>

#include "log.h"

namespace gbos::vpd {
namespace {

constexpr char kMagic[8] = {'G', 'B', 'O', 'S', 'V', 'P', 'D', '\n'};
constexpr uint32_t kVersion = 1;
// Largest file a valid store can produce: header, payload bound, CRC.
constexpr size_t kMaxFileBytes = sizeof(kMagic) + 4 + 4 + kMaxPayloadBytes + 4;

std::array<uint32_t, 256> MakeCrcTable() {
    std::array<uint32_t, 256> table{};
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        table[i] = c;
    }
    return table;
}

void PutU32(std::vector<uint8_t>* out, uint32_t v) {
    for (int i = 0; i < 4; i++) out->push_back(static_cast<uint8_t>(v >> (8 * i)));
}

bool GetU32(const std::vector<uint8_t>& in, size_t* pos, uint32_t* v) {
    if (in.size() - *pos < 4) return false;
    *v = 0;
    for (int i = 0; i < 4; i++) *v |= static_cast<uint32_t>(in[*pos + i]) << (8 * i);
    *pos += 4;
    return true;
}

bool GetBytes(const std::vector<uint8_t>& in, size_t* pos, size_t n, std::string* s) {
    if (in.size() - *pos < n) return false;
    s->assign(reinterpret_cast<const char*>(in.data() + *pos), n);
    *pos += n;
    return true;
}

std::string Errno(const char* what, const std::string& path) {
    return std::string(what) + " " + path + ": " + strerror(errno);
}

bool WriteAll(int fd, const uint8_t* data, size_t size) {
    while (size > 0) {
        ssize_t n = TEMP_FAILURE_RETRY(write(fd, data, size));
        if (n <= 0) return false;
        data += n;
        size -= static_cast<size_t>(n);
    }
    return true;
}

}  // namespace

uint32_t Crc32(const uint8_t* data, size_t size) {
    static const std::array<uint32_t, 256> table = MakeCrcTable();
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < size; i++) c = table[(c ^ data[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

bool IsValidKey(const std::string& key) {
    if (key.empty() || key.size() > kMaxKeyBytes) return false;
    for (unsigned char ch : key) {
        if (ch < 0x21 || ch > 0x7E || ch == '"' || ch == '=') return false;
    }
    return true;
}

bool IsValidValue(const std::string& value) {
    return value.find('\0') == std::string::npos;
}

size_t PayloadBytes(const std::vector<Entry>& entries) {
    size_t total = 0;
    for (const Entry& e : entries) total += 4 + e.key.size() + 4 + e.value.size();
    return total;
}

std::vector<uint8_t> RwStore::Encode(const std::vector<Entry>& entries) {
    std::vector<uint8_t> out(kMagic, kMagic + sizeof(kMagic));
    PutU32(&out, kVersion);
    PutU32(&out, static_cast<uint32_t>(entries.size()));
    for (const Entry& e : entries) {
        PutU32(&out, static_cast<uint32_t>(e.key.size()));
        out.insert(out.end(), e.key.begin(), e.key.end());
        PutU32(&out, static_cast<uint32_t>(e.value.size()));
        out.insert(out.end(), e.value.begin(), e.value.end());
    }
    PutU32(&out, Crc32(out.data(), out.size()));
    return out;
}

std::optional<std::vector<Entry>> RwStore::Decode(const std::vector<uint8_t>& data,
                                                  std::string* why) {
    if (data.size() < sizeof(kMagic) + 12 || memcmp(data.data(), kMagic, sizeof(kMagic)) != 0) {
        *why = "bad magic";
        return std::nullopt;
    }
    size_t body = data.size() - 4;
    size_t pos = body;
    uint32_t crc;
    GetU32(data, &pos, &crc);
    if (crc != Crc32(data.data(), body)) {
        *why = "CRC mismatch";
        return std::nullopt;
    }
    std::vector<uint8_t> in(data.begin(), data.begin() + body);
    pos = sizeof(kMagic);
    uint32_t version, count;
    if (!GetU32(in, &pos, &version) || version != kVersion) {
        *why = "unsupported version";
        return std::nullopt;
    }
    if (!GetU32(in, &pos, &count)) {
        *why = "truncated header";
        return std::nullopt;
    }
    std::vector<Entry> entries;
    for (uint32_t i = 0; i < count; i++) {
        uint32_t len;
        Entry e;
        if (!GetU32(in, &pos, &len) || !GetBytes(in, &pos, len, &e.key) ||
            !GetU32(in, &pos, &len) || !GetBytes(in, &pos, len, &e.value)) {
            *why = "truncated entry";
            return std::nullopt;
        }
        if (!IsValidKey(e.key) || !IsValidValue(e.value)) {
            *why = "invalid entry";
            return std::nullopt;
        }
        for (const Entry& prev : entries) {
            if (prev.key == e.key) {
                *why = "duplicate key";
                return std::nullopt;
            }
        }
        entries.push_back(std::move(e));
    }
    if (pos != in.size()) {
        *why = "trailing bytes";
        return std::nullopt;
    }
    return entries;
}

RwStore::RwStore(std::string dir) : dir_(std::move(dir)) {}

Result RwStore::Load() {
    loaded_ = false;
    entries_.clear();
    const std::string file = path();
    int fd = TEMP_FAILURE_RETRY(open(file.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW));
    if (fd < 0) {
        if (errno == ENOENT) {
            struct stat st;
            if (stat(dir_.c_str(), &st) != 0 || !S_ISDIR(st.st_mode)) {
                return {Status::kIoError, "store directory " + dir_ + " is missing"};
            }
            loaded_ = true;
            return {};
        }
        return {Status::kIoError, Errno("cannot open", file)};
    }
    std::vector<uint8_t> data;
    uint8_t buf[4096];
    bool too_big = false;
    for (;;) {
        ssize_t n = TEMP_FAILURE_RETRY(read(fd, buf, sizeof(buf)));
        if (n < 0) {
            Result r{Status::kIoError, Errno("cannot read", file)};
            close(fd);
            return r;
        }
        if (n == 0) break;
        if (data.size() + static_cast<size_t>(n) > kMaxFileBytes) {
            too_big = true;
            break;
        }
        data.insert(data.end(), buf, buf + n);
    }
    close(fd);

    std::string why = "larger than any valid store";
    std::optional<std::vector<Entry>> decoded;
    if (!too_big) decoded = Decode(data, &why);
    if (!decoded) {
        const std::string aside = dir_ + "/" + kCorruptName;
        if (rename(file.c_str(), aside.c_str()) != 0) {
            return {Status::kIoError, "corrupt store (" + why + ") cannot be moved aside: " +
                                          Errno("rename", file)};
        }
        LOGE("RW_VPD store %s is corrupt (%s); moved to %s, starting empty", file.c_str(),
             why.c_str(), aside.c_str());
        loaded_ = true;
        return {};
    }
    entries_ = std::move(*decoded);
    loaded_ = true;
    return {};
}

std::optional<std::string> RwStore::Get(const std::string& key) const {
    for (const Entry& e : entries_) {
        if (e.key == key) return e.value;
    }
    return std::nullopt;
}

Result RwStore::Apply(const std::vector<Update>& updates) {
    if (!loaded_) return {Status::kIoError, "RW_VPD store is not available"};
    std::vector<Entry> next = entries_;
    for (const Update& u : updates) {
        if (!IsValidKey(u.key)) return {Status::kInvalidArgument, "invalid key \"" + u.key + "\""};
        if (u.value && !IsValidValue(*u.value)) {
            return {Status::kInvalidArgument, "value of " + u.key + " contains a NUL byte"};
        }
        auto it = next.begin();
        while (it != next.end() && it->key != u.key) ++it;
        if (!u.value) {
            if (it != next.end()) next.erase(it);
        } else if (it != next.end()) {
            it->value = *u.value;
        } else {
            next.push_back({u.key, *u.value});
        }
    }
    if (PayloadBytes(next) > kMaxPayloadBytes) {
        return {Status::kNoSpace, "RW_VPD would exceed " + std::to_string(kMaxPayloadBytes) +
                                      " bytes"};
    }
    bool replaced = false;
    Result r = Write(next, &replaced);
    // Once the rename happened the file holds the new entries even if syncing the directory
    // failed, so memory follows the file and the caller still learns the update is not durable.
    if (replaced) entries_ = std::move(next);
    return r;
}

Result RwStore::Write(const std::vector<Entry>& entries, bool* replaced) {
    *replaced = false;
    const std::vector<uint8_t> data = Encode(entries);
    const std::string tmp = dir_ + "/" + kTempName;
    const std::string file = path();

    int dirfd = TEMP_FAILURE_RETRY(open(dir_.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC));
    if (dirfd < 0) return {Status::kIoError, Errno("cannot open", dir_)};
    int fd = TEMP_FAILURE_RETRY(
            open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC | O_NOFOLLOW, 0600));
    if (fd < 0) {
        Result r{Status::kIoError, Errno("cannot create", tmp)};
        close(dirfd);
        return r;
    }
    bool ok = WriteAll(fd, data.data(), data.size()) && fsync(fd) == 0;
    Result r;
    if (!ok) r = {Status::kIoError, Errno("cannot write", tmp)};
    if (close(fd) != 0 && ok) {
        ok = false;
        r = {Status::kIoError, Errno("cannot close", tmp)};
    }
    if (ok && rename(tmp.c_str(), file.c_str()) != 0) {
        ok = false;
        r = {Status::kIoError, Errno("cannot rename", tmp)};
    }
    if (!ok) {
        unlink(tmp.c_str());
        close(dirfd);
        return r;
    }
    *replaced = true;
    // The rename is durable only once the directory is synced.
    if (fsync(dirfd) != 0) r = {Status::kIoError, Errno("cannot sync", dir_)};
    close(dirfd);
    return r;
}

}  // namespace gbos::vpd
