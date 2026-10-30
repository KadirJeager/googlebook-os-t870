// Host tests of the parts of gbos-vpd-executor that do not need binder: the RW store on a real
// file system and the RO derivation. Built and run by build.sh before the device binary.
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cstring>
#include <fstream>
#include <iterator>
#include <map>
#include <string>
#include <vector>

#include "../src/ro_source.h"
#include "../src/vpd_store.h"

using namespace gbos::vpd;

static int g_failures = 0;
#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            g_failures++;                                                    \
        }                                                                    \
    } while (0)

static std::string MakeTempDir() {
    const char* base = getenv("TEST_TMPDIR");
    std::string tmpl = std::string(base ? base : "/tmp") + "/gbos-vpd-test-XXXXXX";
    std::vector<char> buf(tmpl.begin(), tmpl.end());
    buf.push_back('\0');
    if (mkdtemp(buf.data()) == nullptr) {
        perror("mkdtemp");
        exit(2);
    }
    return buf.data();
}

static void RemoveTree(const std::string& dir) {
    if (DIR* d = opendir(dir.c_str())) {
        while (dirent* e = readdir(d)) {
            if (strcmp(e->d_name, ".") && strcmp(e->d_name, "..")) {
                unlink((dir + "/" + e->d_name).c_str());
            }
        }
        closedir(d);
    }
    rmdir(dir.c_str());
}

static std::vector<uint8_t> ReadFile(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>()};
}

static void WriteFile(const std::string& path, const std::vector<uint8_t>& data) {
    std::ofstream f(path, std::ios::binary | std::ios::trunc);
    f.write(reinterpret_cast<const char*>(data.data()), data.size());
}

static bool Exists(const std::string& path) {
    struct stat st;
    return stat(path.c_str(), &st) == 0;
}

static void TestCrc() {
    const char* s = "123456789";  // standard CRC-32 check value
    CHECK(Crc32(reinterpret_cast<const uint8_t*>(s), 9) == 0xCBF43926u);
}

static void TestKeyRules() {
    CHECK(IsValidKey("activate_date"));
    CHECK(IsValidKey("ActivateDate"));
    CHECK(IsValidKey("a.b-c_1"));
    CHECK(!IsValidKey(""));
    CHECK(!IsValidKey("a=b"));
    CHECK(!IsValidKey("a\"b"));
    CHECK(!IsValidKey("a b"));
    CHECK(!IsValidKey("ü"));
    CHECK(IsValidKey(std::string(kMaxKeyBytes, 'k')));
    CHECK(!IsValidKey(std::string(kMaxKeyBytes + 1, 'k')));
    CHECK(IsValidValue(""));
    CHECK(IsValidValue("2026-43 \"quoted\" = ünicode\n"));
    CHECK(!IsValidValue(std::string("a\0b", 3)));
}

static void TestEncodeDecode() {
    std::vector<Entry> in = {{"activate_date", "2026-43"}, {"empty", ""}, {"x", "y=z\n\"q\""}};
    std::string why;
    auto out = RwStore::Decode(RwStore::Encode(in), &why);
    CHECK(out.has_value());
    CHECK(out && out->size() == 3);
    for (size_t i = 0; out && i < in.size(); i++) {
        CHECK((*out)[i].key == in[i].key && (*out)[i].value == in[i].value);
    }
    auto empty = RwStore::Decode(RwStore::Encode({}), &why);
    CHECK(empty && empty->empty());

    std::vector<uint8_t> bad = RwStore::Encode(in);
    bad[20] ^= 1;
    CHECK(!RwStore::Decode(bad, &why) && why == "CRC mismatch");
    CHECK(!RwStore::Decode({1, 2, 3}, &why) && why == "bad magic");
}

static void TestMissingDirectory() {
    std::string dir = MakeTempDir();
    RwStore store(dir + "/missing");
    Result r = store.Load();
    CHECK(r.status == Status::kIoError);
    CHECK(!store.loaded());
    CHECK(store.Apply({{"k", "v"}}).status == Status::kIoError);
    RemoveTree(dir);
}

static void TestLifecycle() {
    std::string dir = MakeTempDir();
    {
        RwStore store(dir);
        CHECK(store.Load().ok());
        CHECK(store.entries().empty());
        CHECK(!Exists(store.path()));
        CHECK(!store.Get("activate_date"));

        CHECK(store.Apply({{"activate_date", "2026-43"}, {"b", "1"}}).ok());
        CHECK(store.Get("activate_date") == std::optional<std::string>("2026-43"));
        CHECK(Exists(store.path()));
        CHECK(!Exists(dir + "/" + RwStore::kTempName));
        struct stat st;
        CHECK(stat(store.path().c_str(), &st) == 0 && (st.st_mode & 0777) == 0600);

        // Overwrite keeps the position, a new key is appended, a null value deletes.
        CHECK(store.Apply({{"activate_date", "2026-44"}, {"c", "3"}, {"b", std::nullopt}}).ok());
        CHECK(store.entries().size() == 2);
        CHECK(store.entries()[0].key == "activate_date" && store.entries()[0].value == "2026-44");
        CHECK(store.entries()[1].key == "c");
        // Deleting a missing key changes nothing and succeeds.
        CHECK(store.Apply({{"nope", std::nullopt}}).ok());
        CHECK(store.Apply({}).ok());
    }
    {
        RwStore again(dir);  // what a restarted service sees
        CHECK(again.Load().ok());
        CHECK(again.entries().size() == 2);
        CHECK(again.Get("activate_date") == std::optional<std::string>("2026-44"));
        CHECK(again.Get("c") == std::optional<std::string>("3"));
        CHECK(!again.Get("b"));
    }
    RemoveTree(dir);
}

static void TestAllOrNothing() {
    std::string dir = MakeTempDir();
    RwStore store(dir);
    CHECK(store.Load().ok());
    CHECK(store.Apply({{"a", "1"}}).ok());
    std::vector<uint8_t> before = ReadFile(store.path());

    Result r = store.Apply({{"b", "2"}, {"bad=key", "3"}});
    CHECK(r.status == Status::kInvalidArgument);
    r = store.Apply({{"b", std::string("x\0y", 3)}});
    CHECK(r.status == Status::kInvalidArgument);
    r = store.Apply({{"big", std::string(kMaxPayloadBytes, 'v')}});
    CHECK(r.status == Status::kNoSpace);

    CHECK(store.entries().size() == 1 && !store.Get("b"));
    CHECK(ReadFile(store.path()) == before);

    // Exactly at the bound is accepted: payload = (4+1+4+1) for "a" + (4+3+4+n) for "big".
    size_t n = kMaxPayloadBytes - (4 + 1 + 4 + 1) - (4 + 3 + 4);
    CHECK(store.Apply({{"big", std::string(n, 'v')}}).ok());
    CHECK(store.Apply({{"one", "more"}}).status == Status::kNoSpace);
    RemoveTree(dir);
}

static void TestCorruptFileIsSetAside() {
    std::string dir = MakeTempDir();
    {
        RwStore store(dir);
        CHECK(store.Load().ok());
        CHECK(store.Apply({{"a", "1"}}).ok());
    }
    std::vector<uint8_t> data = ReadFile(dir + "/rw_vpd");
    data.resize(data.size() - 3);  // a torn file
    WriteFile(dir + "/rw_vpd", data);
    RwStore store(dir);
    CHECK(store.Load().ok());
    CHECK(store.entries().empty());
    CHECK(!Exists(dir + "/rw_vpd"));
    CHECK(ReadFile(dir + "/rw_vpd.corrupt") == data);
    CHECK(store.Apply({{"a", "2"}}).ok());
    CHECK(store.Get("a") == std::optional<std::string>("2"));
    RemoveTree(dir);
}

static void TestStaleTempFileIgnored() {
    // A crash between writing rw_vpd.tmp and the rename must leave the old store in effect.
    std::string dir = MakeTempDir();
    {
        RwStore store(dir);
        CHECK(store.Load().ok());
        CHECK(store.Apply({{"a", "old"}}).ok());
    }
    WriteFile(dir + "/rw_vpd.tmp", RwStore::Encode({{"a", "new"}}));
    RwStore store(dir);
    CHECK(store.Load().ok());
    CHECK(store.Get("a") == std::optional<std::string>("old"));
    CHECK(store.Apply({{"b", "1"}}).ok());  // the next write replaces the stale temp file
    CHECK(!Exists(dir + "/rw_vpd.tmp"));
    RemoveTree(dir);
}

static void TestReadOnlyDirectory() {
    if (geteuid() == 0) return;  // root ignores the permission bits
    std::string dir = MakeTempDir();
    RwStore store(dir);
    CHECK(store.Load().ok());
    CHECK(store.Apply({{"a", "1"}}).ok());
    chmod(dir.c_str(), 0500);
    Result r = store.Apply({{"a", "2"}});
    CHECK(r.status == Status::kIoError);
    CHECK(store.Get("a") == std::optional<std::string>("1"));
    chmod(dir.c_str(), 0700);
    RemoveTree(dir);
}

static void TestRoDerivation() {
    std::map<std::string, std::string> props = {
            {"ro.boot.serialno", "R52T00000XX"},
            {"ro.serialno", "R52T00000XX"},
            {"ro.boot.em.model", "SM-T870"},
            {"ro.product.vendor.model", "SM-T870"},
    };
    auto reader = [&](const char* n) { return props.count(n) ? props[n] : std::string(); };
    std::vector<Entry> e = DeriveRoEntries(reader);
    CHECK(e.size() == 2);
    CHECK(e.size() == 2 && e[0].key == "serial_number" && e[0].value == "R52T00000XX");
    CHECK(e.size() == 2 && e[1].key == "model_name" && e[1].value == "SM-T870");

    // Fallbacks, and values that are not real are left out instead of invented.
    props = {{"ro.serialno", " R52T00000XX\n"}, {"ro.product.vendor.model", "SM-T870"}};
    e = DeriveRoEntries(reader);
    CHECK(e.size() == 2 && e[0].value == "R52T00000XX" && e[1].value == "SM-T870");
    props = {{"ro.boot.serialno", "unknown"}, {"ro.serialno", ""}};
    e = DeriveRoEntries(reader);
    CHECK(e.empty());
}

int main() {
    TestCrc();
    TestKeyRules();
    TestEncodeDecode();
    TestMissingDirectory();
    TestLifecycle();
    TestAllOrNothing();
    TestCorruptFileIsSetAside();
    TestStaleTempFileIgnored();
    TestReadOnlyDirectory();
    TestRoDerivation();
    if (g_failures) {
        fprintf(stderr, "%d check(s) failed\n", g_failures);
        return 1;
    }
    printf("host tests passed\n");
    return 0;
}
