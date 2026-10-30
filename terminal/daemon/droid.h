// Checks for requests apps send through the Terminal for the droid account (config.h,
// kDroidAccount): a destination inside its home and the file data that goes there. Header only
// and free of Android APIs, so test/birim builds it on the host.
#pragma once

#include <stdint.h>

#include <string>

namespace gbos {

// A path relative to the droid home that names a file below it, by the rules of Android's
// terminal (GuestPathSanitizer): not empty, not absolute, no "." or ".." segment, no empty
// segment, no trailing '/', no backslash or control character, at most 255 bytes a segment and
// 1024 in all. Returns an empty string when acceptable, else the reason.
inline std::string droid_path_problem(const std::string& rel) {
    if (rel.empty()) return "the destination is empty";
    if (rel.size() > 1024) return "the destination is longer than 1024 bytes";
    if (rel[0] == '/') return "the destination must be relative to the home directory";
    if (rel.back() == '/') return "the destination must name a file";
    size_t start = 0;
    while (start <= rel.size()) {
        size_t end = rel.find('/', start);
        if (end == std::string::npos) end = rel.size();
        std::string seg = rel.substr(start, end - start);
        if (seg.empty()) return "the destination has an empty path segment";
        if (seg == "." || seg == "..") return "the destination may not contain . or ..";
        if (seg.size() > 255) return "a path segment is longer than 255 bytes";
        for (unsigned char c : seg) {
            if (c < 0x20 || c == 0x7f || c == '\\') return "the destination contains a control character or backslash";
        }
        start = end + 1;
    }
    return {};
}

// Standard base64 (RFC 4648 section 4, padding required, no whitespace). False on bad input.
inline bool base64_decode(const std::string& in, std::string* out) {
    auto value = [](char c) -> int {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '+') return 62;
        if (c == '/') return 63;
        return -1;
    };
    out->clear();
    if (in.size() % 4 != 0) return false;
    out->reserve(in.size() / 4 * 3);
    for (size_t i = 0; i < in.size(); i += 4) {
        int v[4];
        int pad = 0;
        for (int k = 0; k < 4; k++) {
            char c = in[i + k];
            if (c == '=') {
                // Padding only in the last group, only at its end.
                if (i + 4 != in.size() || k < 2) return false;
                v[k] = 0;
                pad++;
            } else {
                if (pad) return false;
                v[k] = value(c);
                if (v[k] < 0) return false;
            }
        }
        uint32_t n = (uint32_t(v[0]) << 18) | (uint32_t(v[1]) << 12) | (uint32_t(v[2]) << 6) | uint32_t(v[3]);
        out->push_back(static_cast<char>(n >> 16));
        if (pad < 2) out->push_back(static_cast<char>((n >> 8) & 0xff));
        if (pad < 1) out->push_back(static_cast<char>(n & 0xff));
    }
    return true;
}

}  // namespace gbos
