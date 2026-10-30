// Host unit test of daemon/droid.h: the destination rules for files apps copy into the droid
// home and the base64 decoder of their data. Run by test/birim.sh.
#include <stdio.h>

#include <string>

#include "droid.h"

using gbos::base64_decode;
using gbos::droid_path_problem;

static int failures = 0;

static void expect(bool ok, const char* what) {
    if (!ok) {
        fprintf(stderr, "FAIL: %s\n", what);
        failures++;
    }
}

int main() {
    // What Antigravity sends (dest_path=.setup_terminal.sh) and ordinary nested paths.
    expect(droid_path_problem(".setup_terminal.sh").empty(), "dotfile accepted");
    expect(droid_path_problem("bin/tool.sh").empty(), "nested accepted");
    expect(droid_path_problem("a/b/c/d").empty(), "deep accepted");
    expect(droid_path_problem(std::string(255, 'x')).empty(), "255-byte segment accepted");
    // Refused.
    expect(!droid_path_problem("").empty(), "empty refused");
    expect(!droid_path_problem("/etc/passwd").empty(), "absolute refused");
    expect(!droid_path_problem("../x").empty(), "parent refused");
    expect(!droid_path_problem("a/../../x").empty(), "inner parent refused");
    expect(!droid_path_problem("a/./b").empty(), "dot segment refused");
    expect(!droid_path_problem(".").empty(), "dot refused");
    expect(!droid_path_problem("a//b").empty(), "empty segment refused");
    expect(!droid_path_problem("dir/").empty(), "trailing slash refused");
    expect(!droid_path_problem("a\\b").empty(), "backslash refused");
    expect(!droid_path_problem("a\nb").empty(), "newline refused");
    expect(!droid_path_problem(std::string("a\0b", 3)).empty(), "NUL refused");
    expect(!droid_path_problem("a\x7f").empty(), "DEL refused");
    expect(!droid_path_problem(std::string(256, 'x')).empty(), "256-byte segment refused");
    expect(!droid_path_problem(std::string(1025, 'a')).empty(), "1025 bytes refused");

    std::string out;
    expect(base64_decode("", &out) && out.empty(), "empty base64");
    expect(base64_decode("aGk=", &out) && out == "hi", "one pad");
    expect(base64_decode("aGkh", &out) && out == "hi!", "no pad");
    expect(base64_decode("aA==", &out) && out == "h", "two pads");
    expect(base64_decode("AP8A/w==", &out) && out == std::string("\x00\xff\x00\xff", 4), "binary");
    expect(!base64_decode("aGk", &out), "length not multiple of 4 refused");
    expect(!base64_decode("a===", &out), "three pads refused");
    expect(!base64_decode("aG=k", &out), "pad in the middle refused");
    expect(!base64_decode("aGk=aGk=", &out), "pad before the end refused");
    expect(!base64_decode("aG\nk", &out), "whitespace refused");
    expect(!base64_decode("aG-_", &out), "url alphabet refused");

    if (failures) {
        fprintf(stderr, "droid_test: %d failed\n", failures);
        return 1;
    }
    printf("droid_test: ok\n");
    return 0;
}
