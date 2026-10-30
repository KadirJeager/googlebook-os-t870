/*
 * libprovide_meta.so: gives a tool that packs a directory tree (mkfs.erofs) the ownership, modes
 * and extended attributes captured by libcapture_meta.so when the tree was extracted as an
 * unprivileged user, so the image it builds carries the original metadata.
 *
 *   LD_PRELOAD=libprovide_meta.so META_LOG=x.meta META_OLD_PREFIX=<capture root> \
 *       META_NEW_PREFIX=<tree being packed> mkfs.erofs ... out.img <tree>
 *
 * META_LOG rows (tab separated, written by libcapture_meta.so and apply_patches._extract_with_meta):
 *   ROOT  <capture root>
 *   CHOWN <path> <uid> <gid>
 *   CHMOD <path> <octal mode>
 *   XATTR <path> <name> <hex value>        any number per path (SELinux label, capabilities, ...)
 * Paths under META_OLD_PREFIX are moved to META_NEW_PREFIX.
 *
 * Inside the tree (META_NEW_PREFIX, or every captured path when no prefix is given) the captured
 * metadata is the only truth: lstat reports the captured owner and permission bits, the xattr
 * calls list and return exactly the captured attributes, and a path without a captured owner (or,
 * other than a symlink, without a captured mode) makes the library stop the tool (exit 70)
 * instead of letting the build user's uid or a missing label into the image. Outside the tree every call goes to the C library unchanged.
 * A META_LOG that cannot be read or holds a malformed row also stops the tool: a silently empty
 * table would give an image without labels.
 *
 * Interposed: lstat, lstat64, fstatat, fstatat64 (AT_SYMLINK_NOFOLLOW lookups by path),
 * llistxattr, lgetxattr, listxattr, getxattr. Sizes are not limited: the table grows as needed.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/xattr.h>
#include <unistd.h>

#define EXIT_META 70

struct xattr {
    char *name;
    unsigned char *value;
    size_t len;
};

struct entry {
    char *path;
    struct entry *next;          /* hash chain */
    uid_t uid;
    gid_t gid;
    mode_t mode;
    int has_owner;
    int has_mode;
    struct xattr *xattrs;
    size_t nxattrs;
};

static struct entry **table;
static size_t table_size;
static size_t table_count;
static int active;               /* META_LOG was given and loaded */
static char *tree;               /* paths under this prefix are governed by the table */
static size_t tree_len;

static void die(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("libprovide_meta: ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    _exit(EXIT_META);
}

static void *xmalloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) die("out of memory");
    return p;
}

static char *xstrdup(const char *s) {
    char *p = strdup(s);
    if (!p) die("out of memory");
    return p;
}

static uint64_t hash(const char *s) {
    uint64_t h = 1469598103934665603ULL;  /* FNV-1a */
    for (; *s; s++) {
        h ^= (unsigned char)*s;
        h *= 1099511628211ULL;
    }
    return h;
}

/* Collapses "//", "/./" and a trailing "/" so the tool's spelling of a path matches the log's. */
static char *normalize(const char *in) {
    size_t n = strlen(in);
    char *out = xmalloc(n + 1);
    size_t o = 0;
    for (size_t i = 0; i < n; i++) {
        if (in[i] == '/' && o > 0 && out[o - 1] == '/') continue;
        if (in[i] == '.' && (i + 1 == n || in[i + 1] == '/') && o > 0 && out[o - 1] == '/') {
            continue;  /* "/./" or trailing "/." */
        }
        out[o++] = in[i];
    }
    while (o > 1 && out[o - 1] == '/') o--;
    out[o] = 0;
    return out;
}

static void grow(void) {
    size_t nsize = table_size ? table_size * 2 : 4096;
    struct entry **nt = calloc(nsize, sizeof(*nt));
    if (!nt) die("out of memory");
    for (size_t i = 0; i < table_size; i++) {
        struct entry *e = table[i];
        while (e) {
            struct entry *next = e->next;
            size_t b = hash(e->path) & (nsize - 1);
            e->next = nt[b];
            nt[b] = e;
            e = next;
        }
    }
    free(table);
    table = nt;
    table_size = nsize;
}

static struct entry *lookup(const char *norm) {
    if (!table_size) return NULL;
    for (struct entry *e = table[hash(norm) & (table_size - 1)]; e; e = e->next) {
        if (strcmp(e->path, norm) == 0) return e;
    }
    return NULL;
}

static struct entry *get_or_create(const char *norm) {
    struct entry *e = lookup(norm);
    if (e) return e;
    if (table_count * 2 >= table_size) grow();
    e = xmalloc(sizeof(*e));
    memset(e, 0, sizeof(*e));
    e->path = xstrdup(norm);
    size_t b = hash(norm) & (table_size - 1);
    e->next = table[b];
    table[b] = e;
    table_count++;
    return e;
}

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void set_xattr(struct entry *e, const char *name, const char *hex, const char *where) {
    size_t hl = strlen(hex);
    if (hl % 2) die("%s: odd-length xattr value for %s", where, name);
    if (strlen(name) > XATTR_NAME_MAX) die("%s: xattr name too long: %s", where, name);
    size_t len = hl / 2;
    if (len > XATTR_SIZE_MAX) die("%s: xattr %s larger than XATTR_SIZE_MAX", where, name);
    unsigned char *val = xmalloc(len);
    for (size_t i = 0; i < len; i++) {
        int hi = hexval(hex[2 * i]), lo = hexval(hex[2 * i + 1]);
        if (hi < 0 || lo < 0) die("%s: bad hex in xattr %s", where, name);
        val[i] = (unsigned char)(hi << 4 | lo);
    }
    for (size_t i = 0; i < e->nxattrs; i++) {
        if (strcmp(e->xattrs[i].name, name) == 0) {  /* a later row replaces an earlier one */
            free(e->xattrs[i].value);
            e->xattrs[i].value = val;
            e->xattrs[i].len = len;
            return;
        }
    }
    e->xattrs = realloc(e->xattrs, (e->nxattrs + 1) * sizeof(*e->xattrs));
    if (!e->xattrs) die("out of memory");
    e->xattrs[e->nxattrs].name = xstrdup(name);
    e->xattrs[e->nxattrs].value = val;
    e->xattrs[e->nxattrs].len = len;
    e->nxattrs++;
}

static int parse_ul(const char *s, int base, unsigned long *out) {
    char *end;
    errno = 0;
    unsigned long v = strtoul(s, &end, base);
    if (errno || end == s || *end) return -1;
    *out = v;
    return 0;
}

__attribute__((constructor))
static void init(void) {
    const char *log = getenv("META_LOG");
    if (!log || !*log) return;
    const char *old_prefix = getenv("META_OLD_PREFIX");
    const char *new_prefix = getenv("META_NEW_PREFIX");
    if ((old_prefix && *old_prefix) != (new_prefix && *new_prefix)) {
        die("META_OLD_PREFIX and META_NEW_PREFIX go together");
    }
    char *old_norm = old_prefix && *old_prefix ? normalize(old_prefix) : NULL;
    size_t old_len = old_norm ? strlen(old_norm) : 0;
    if (new_prefix && *new_prefix) {
        tree = normalize(new_prefix);
        tree_len = strlen(tree);
    }

    FILE *f = fopen(log, "re");
    if (!f) die("cannot read META_LOG %s: %s", log, strerror(errno));
    char *line = NULL;
    size_t cap = 0;
    ssize_t n;
    unsigned long lineno = 0;
    char where[64];
    while ((n = getline(&line, &cap, f)) != -1) {
        lineno++;
        snprintf(where, sizeof(where), "META_LOG line %lu", lineno);
        while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
        if (n == 0) continue;
        char *fields[5] = {0};
        int nf = 0;
        char *save = NULL;
        for (char *tok = strtok_r(line, "\t", &save); tok && nf < 5; tok = strtok_r(NULL, "\t", &save)) {
            fields[nf++] = tok;
        }
        if (nf < 2) die("%s: too few fields", where);
        const char *type = fields[0];
        char *norm = normalize(fields[1]);
        char *path = norm;
        if (old_norm && strncmp(norm, old_norm, old_len) == 0 &&
            (norm[old_len] == 0 || norm[old_len] == '/')) {
            path = xmalloc(tree_len + strlen(norm + old_len) + 1);
            strcpy(path, tree);
            strcat(path, norm + old_len);
            free(norm);
        }
        if (strcmp(type, "ROOT") == 0) {
            if (nf != 2) die("%s: ROOT takes one field", where);
        } else if (strcmp(type, "CHOWN") == 0) {
            unsigned long u, g;
            if (nf != 4 || parse_ul(fields[2], 10, &u) || parse_ul(fields[3], 10, &g)) {
                die("%s: bad CHOWN row", where);
            }
            struct entry *e = get_or_create(path);
            e->uid = (uid_t)u;
            e->gid = (gid_t)g;
            e->has_owner = 1;
        } else if (strcmp(type, "CHMOD") == 0) {
            unsigned long m;
            if (nf != 3 || parse_ul(fields[2], 8, &m)) die("%s: bad CHMOD row", where);
            struct entry *e = get_or_create(path);
            e->mode = (mode_t)m;
            e->has_mode = 1;
        } else if (strcmp(type, "XATTR") == 0) {
            if (nf != 4) die("%s: bad XATTR row", where);
            set_xattr(get_or_create(path), fields[2], fields[3], where);
        } else {
            die("%s: unknown row type %s", where, type);
        }
        free(path);
    }
    if (ferror(f)) die("error reading META_LOG %s", log);
    free(line);
    fclose(f);
    free(old_norm);
    active = 1;
}

/* Absolute, normalized spelling of (dirfd, path), or NULL when it cannot be formed. */
static char *resolve(int dirfd, const char *path) {
    if (!path) return NULL;
    if (path[0] == '/') return normalize(path);
    char base[PATH_MAX];
    if (dirfd == AT_FDCWD) {
        if (!getcwd(base, sizeof(base))) return NULL;
    } else {
        char link[64];
        snprintf(link, sizeof(link), "/proc/self/fd/%d", dirfd);
        ssize_t l = readlink(link, base, sizeof(base) - 1);
        if (l < 0) return NULL;
        base[l] = 0;
    }
    size_t bl = strlen(base), pl = strlen(path);
    char *joined = xmalloc(bl + pl + 2);
    memcpy(joined, base, bl);
    joined[bl] = '/';
    memcpy(joined + bl + 1, path, pl + 1);
    char *norm = normalize(joined);
    free(joined);
    return norm;
}

static int in_tree(const char *norm) {
    if (!tree) return lookup(norm) != NULL;
    return strncmp(norm, tree, tree_len) == 0 && (norm[tree_len] == 0 || norm[tree_len] == '/');
}

/* The entry governing norm, or NULL when the path is outside the tree. Stops the tool when a
 * path in the tree has no captured owner (fsck.erofs records one for every inode). */
static struct entry *governing(const char *norm) {
    if (!active || !norm || !in_tree(norm)) return NULL;
    struct entry *e = lookup(norm);
    if (!e || !e->has_owner) die("%s is in the tree but has no captured %s", norm, !e ? "metadata" : "owner");
    return e;
}

/* fsck.erofs sets no mode on symlinks (their permission bits mean nothing); every other inode
 * must have a captured mode. */
static void apply(struct entry *e, uid_t *uid, gid_t *gid, mode_t *mode) {
    *uid = e->uid;
    *gid = e->gid;
    if (e->has_mode) {
        *mode = (*mode & S_IFMT) | (e->mode & ~S_IFMT);
    } else if (!S_ISLNK(*mode)) {
        die("%s is in the tree but has no captured mode", e->path);
    }
}

#define REAL(name, ret, ...)                                                  \
    static ret (*real_##name)(__VA_ARGS__);                                   \
    if (!real_##name) {                                                       \
        real_##name = (ret (*)(__VA_ARGS__))dlsym(RTLD_NEXT, #name);          \
        if (!real_##name) die("dlsym(%s) failed", #name);                    \
    }

int lstat(const char *path, struct stat *buf) {
    REAL(lstat, int, const char *, struct stat *);
    int r = real_lstat(path, buf);
    if (r == 0 && active) {
        char *norm = resolve(AT_FDCWD, path);
        struct entry *e = governing(norm);
        if (e) apply(e, &buf->st_uid, &buf->st_gid, &buf->st_mode);
        free(norm);
    }
    return r;
}

int lstat64(const char *path, struct stat64 *buf) {
    REAL(lstat64, int, const char *, struct stat64 *);
    int r = real_lstat64(path, buf);
    if (r == 0 && active) {
        char *norm = resolve(AT_FDCWD, path);
        struct entry *e = governing(norm);
        if (e) apply(e, &buf->st_uid, &buf->st_gid, &buf->st_mode);
        free(norm);
    }
    return r;
}

int fstatat(int dirfd, const char *path, struct stat *buf, int flags) {
    REAL(fstatat, int, int, const char *, struct stat *, int);
    int r = real_fstatat(dirfd, path, buf, flags);
    if (r == 0 && active && (flags & AT_SYMLINK_NOFOLLOW) && path && *path) {
        char *norm = resolve(dirfd, path);
        struct entry *e = governing(norm);
        if (e) apply(e, &buf->st_uid, &buf->st_gid, &buf->st_mode);
        free(norm);
    }
    return r;
}

int fstatat64(int dirfd, const char *path, struct stat64 *buf, int flags) {
    REAL(fstatat64, int, int, const char *, struct stat64 *, int);
    int r = real_fstatat64(dirfd, path, buf, flags);
    if (r == 0 && active && (flags & AT_SYMLINK_NOFOLLOW) && path && *path) {
        char *norm = resolve(dirfd, path);
        struct entry *e = governing(norm);
        if (e) apply(e, &buf->st_uid, &buf->st_gid, &buf->st_mode);
        free(norm);
    }
    return r;
}

static ssize_t list_from(struct entry *e, char *list, size_t size) {
    size_t need = 0;
    for (size_t i = 0; i < e->nxattrs; i++) need += strlen(e->xattrs[i].name) + 1;
    if (size == 0) return (ssize_t)need;
    if (size < need) {
        errno = ERANGE;
        return -1;
    }
    char *p = list;
    for (size_t i = 0; i < e->nxattrs; i++) {
        size_t l = strlen(e->xattrs[i].name) + 1;
        memcpy(p, e->xattrs[i].name, l);
        p += l;
    }
    return (ssize_t)need;
}

static ssize_t get_from(struct entry *e, const char *name, void *value, size_t size) {
    for (size_t i = 0; i < e->nxattrs; i++) {
        if (strcmp(e->xattrs[i].name, name) != 0) continue;
        if (size == 0) return (ssize_t)e->xattrs[i].len;
        if (size < e->xattrs[i].len) {
            errno = ERANGE;
            return -1;
        }
        memcpy(value, e->xattrs[i].value, e->xattrs[i].len);
        return (ssize_t)e->xattrs[i].len;
    }
    errno = ENODATA;
    return -1;
}

ssize_t llistxattr(const char *path, char *list, size_t size) {
    REAL(llistxattr, ssize_t, const char *, char *, size_t);
    char *norm = active ? resolve(AT_FDCWD, path) : NULL;
    struct entry *e = governing(norm);
    free(norm);
    return e ? list_from(e, list, size) : real_llistxattr(path, list, size);
}

ssize_t listxattr(const char *path, char *list, size_t size) {
    REAL(listxattr, ssize_t, const char *, char *, size_t);
    char *norm = active ? resolve(AT_FDCWD, path) : NULL;
    struct entry *e = governing(norm);
    free(norm);
    return e ? list_from(e, list, size) : real_listxattr(path, list, size);
}

ssize_t lgetxattr(const char *path, const char *name, void *value, size_t size) {
    REAL(lgetxattr, ssize_t, const char *, const char *, void *, size_t);
    char *norm = active ? resolve(AT_FDCWD, path) : NULL;
    struct entry *e = governing(norm);
    free(norm);
    return e ? get_from(e, name, value, size) : real_lgetxattr(path, name, value, size);
}

ssize_t getxattr(const char *path, const char *name, void *value, size_t size) {
    REAL(getxattr, ssize_t, const char *, const char *, void *, size_t);
    char *norm = active ? resolve(AT_FDCWD, path) : NULL;
    struct entry *e = governing(norm);
    free(norm);
    return e ? get_from(e, name, value, size) : real_getxattr(path, name, value, size);
}
