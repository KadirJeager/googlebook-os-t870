#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/xattr.h>

/* libcapture_meta.so: preloaded into fsck.erofs --extract --preserve --xattrs run as an
 * unprivileged user. The ownership, mode and xattr calls are recorded in META_LOG (rows CHOWN,
 * CHMOD, XATTR; see provide_meta.c) instead of being applied, so the tree can be packed again
 * with its original metadata. A META_LOG that cannot be opened stops the tool (exit 70): an
 * extraction without its record would lose every label. */
static FILE *logf = NULL;
__attribute__((constructor))
static void init(void) {
    const char *p = getenv("META_LOG");
    if (!p || !*p) return;
    logf = fopen(p, "a");
    if (!logf) {
        fprintf(stderr, "libcapture_meta: cannot open META_LOG %s\n", p);
        _exit(70);
    }
}

uid_t geteuid(void) { return 0; }
uid_t getuid(void) { return 0; }

int lchown(const char *path, uid_t owner, gid_t group) {
    if (logf) { fprintf(logf, "CHOWN\t%s\t%u\t%u\n", path, owner, group); fflush(logf); }
    return 0;
}
int chown(const char *path, uid_t owner, gid_t group) {
    if (logf) { fprintf(logf, "CHOWN\t%s\t%u\t%u\n", path, owner, group); fflush(logf); }
    return 0;
}
int chmod(const char *path, mode_t mode) {
    if (logf) { fprintf(logf, "CHMOD\t%s\t0%o\n", path, mode); fflush(logf); }
    return 0;
}
int lsetxattr(const char *path, const char *name, const void *value, size_t size, int flags) {
    (void)flags;
    if (logf) {
        fprintf(logf, "XATTR\t%s\t%s\t", path, name);
        const unsigned char *v = value;
        for (size_t i = 0; i < size; i++) fprintf(logf, "%02x", v[i]);
        fprintf(logf, "\n");
        fflush(logf);
    }
    return 0;
}
int setxattr(const char *path, const char *name, const void *value, size_t size, int flags) {
    return lsetxattr(path, name, value, size, flags);
}
