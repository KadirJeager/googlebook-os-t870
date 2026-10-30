package com.gbos.terminal;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

/**
 * The checks Android's own Linux terminal (com.android.virtualization.terminal,
 * TerminalIntentActivity) applies to requests from other apps, for the same contract here:
 * "Copy &amp; Run" ({@code android-terminal://start?cmd=&dest_path=&background=}) and
 * SHARE_DIRECTORY. Plain Java, no Android API: test/birim runs it on the host.
 *
 * Rules taken from the Terminal app of the Googlebook OS image (VmTerminalAppGoogle in
 * com.google.android.virt, CL3B.260622.273): GuestPathSanitizer, CommandValidator,
 * StringUtils.containsControlChars, SourceUriVerifier's authority parsing, SafUriResolver.
 */
final class AgentRules {
    /** Home of the account apps for Android's terminal log in as (daemon config.h kDroidAccount). */
    static final String DROID_HOME = "/home/droid";

    /** A request that must not run; {@link #reason} is shown to the user. */
    static final class Rejected extends Exception {
        private static final long serialVersionUID = 1L;
        final String reason;

        Rejected(String reason) {
            super(reason);
            this.reason = reason;
        }
    }

    private static final Pattern NON_ASCII = Pattern.compile("\\P{ASCII}");
    private static final Pattern HEREDOC = Pattern.compile("<<");
    private static final Pattern FORKBOMB =
            Pattern.compile(":\\s*\\(\\s*\\)\\s*\\{\\s*:\\s*\\|\\s*:\\s*&\\s*\\}\\s*;\\s*:");
    private static final Pattern BASE64_OBFUSCATION =
            Pattern.compile("(?:base64|b64decode)\\b.*\\|\\s*(?:sh|bash|zsh|eval)\\b", Pattern.CASE_INSENSITIVE);
    private static final Pattern FUNCTION_DEFINITION =
            Pattern.compile("(?:\\bfunction\\s+\\w+|\\w+\\s*\\(\\s*\\)\\s*\\{)");
    private static final Pattern BACKGROUND_PROCESS =
            Pattern.compile("(?<![><&|])&(?![&>])\\s*(?:;|&&|\\|\\||$)|(?:^|;|&&|\\|\\||\\||&)\\s*\\bbg\\b");

    private AgentRules() {
    }

    /** Control characters, and the invisible format characters that disguise text. */
    static boolean containsControlChars(String s) {
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (Character.isISOControl(c)) return true;
            if (c >= 0x200E && c < 0x2010) return true;   // LRM, RLM, ...
            if (c >= 0x202A && c < 0x202F) return true;   // bidi embedding and override
            if (c >= 0x2066 && c < 0x206A) return true;   // bidi isolates
            if ((c >= 0x200B && c < 0x200E) || c == 0xFEFF || c == 0x00AD) return true;  // zero width, BOM, soft hyphen
        }
        return false;
    }

    /**
     * Why a command must not run unattended, or null. Same patterns as Android's terminal: what
     * is shown to the user must be what runs (no hidden text), and it must end when it ends (no
     * background jobs, no definitions, no decoded payloads).
     */
    static String commandProblem(String command) {
        if (NON_ASCII.matcher(command).find()) return "non_ascii";
        if (containsControlChars(command)) return "control";
        if (HEREDOC.matcher(command).find()) return "heredoc";
        if (FORKBOMB.matcher(command).find()) return "forkbomb";
        if (BASE64_OBFUSCATION.matcher(command).find()) return "obfuscated";
        if (FUNCTION_DEFINITION.matcher(command).find()) return "function";
        if (BACKGROUND_PROCESS.matcher(command).find()) return "background";
        return null;
    }

    /**
     * dest_path, relative to the droid home, normalised; the absolute path is
     * {@code DROID_HOME + "/" + result}. Throws for anything that is not a file below the home.
     */
    static String destination(String raw) throws Rejected {
        String path = raw;
        while (path.endsWith("\n") || path.endsWith("\r")) path = path.substring(0, path.length() - 1);
        if (path.trim().isEmpty()) throw new Rejected("Destination path must not be empty");
        if (path.startsWith("/")) throw new Rejected("Destination path must be relative to " + DROID_HOME);
        if (path.endsWith("/")) throw new Rejected("Destination path must name a file");
        if (path.equals(".") || path.endsWith("/.")) throw new Rejected("Destination path must name a file");
        List<String> out = new ArrayList<>();
        for (String seg : path.split("/", -1)) {
            if (seg.isEmpty() || seg.equals(".")) continue;
            if (seg.equals("..")) {
                if (out.isEmpty()) throw new Rejected("Destination path leaves " + DROID_HOME);
                out.remove(out.size() - 1);
                continue;
            }
            if (seg.indexOf('\\') >= 0 || containsControlChars(seg)) {
                throw new Rejected("Invalid path segment in '" + raw + "'");
            }
            if (seg.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 255) {
                throw new Rejected("A path segment is longer than 255 bytes");
            }
            out.add(seg);
        }
        if (out.isEmpty()) throw new Rejected("Destination path must name a file");
        String normalized = String.join("/", out);
        if (normalized.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 1024) {
            throw new Rejected("Destination path is longer than 1024 bytes");
        }
        return normalized;
    }

    /** "10@authority" → (authority, 10); "authority" → (authority, -1). */
    static final class Authority {
        final String name;
        final int userId;

        Authority(String name, int userId) {
            this.name = name;
            this.userId = userId;
        }
    }

    static Authority authority(String authority) throws Rejected {
        if (authority == null || authority.isEmpty()) throw new Rejected("URI authority is missing");
        String[] parts = authority.split("@", -1);
        if (parts.length == 1) return new Authority(parts[0], -1);
        if (parts.length != 2) throw new Rejected("Malformed authority '" + authority + "'");
        String user = parts[0];
        if (user.isEmpty() || (user.length() > 1 && user.charAt(0) == '0')) {
            throw new Rejected("Invalid user id in authority '" + authority + "'");
        }
        int id;
        try {
            id = Integer.parseInt(user);
        } catch (NumberFormatException e) {
            throw new Rejected("Invalid user id in authority '" + authority + "'");
        }
        if (id < 0) throw new Rejected("Invalid user id in authority '" + authority + "'");
        if (parts[1].isEmpty()) throw new Rejected("Empty authority in '" + authority + "'");
        return new Authority(parts[1], id);
    }

    static final String EXTERNAL_STORAGE_DOCUMENTS = "com.android.externalstorage.documents";
    static final String DOWNLOADS_DOCUMENTS = "com.android.providers.downloads.documents";

    /**
     * The path, inside Linux and on Android alike, of a folder picked with the system file
     * picker (a tree URI's document id): "primary:Documents/x" in Android user 10 is
     * /storage/emulated/10/Documents/x. Only the user's shared storage is reachable from Linux;
     * other volumes are refused.
     */
    static String sharedFolder(String provider, String treeDocumentId, int androidUser) throws Rejected {
        if (treeDocumentId == null) throw new Rejected("Not a folder");
        int colon = treeDocumentId.indexOf(':');
        String root = colon < 0 ? treeDocumentId : treeDocumentId.substring(0, colon);
        String rel = colon < 0 ? "" : treeDocumentId.substring(colon + 1);
        String base = "/storage/emulated/" + androidUser;
        String prefix;
        if (EXTERNAL_STORAGE_DOCUMENTS.equals(provider) && root.equalsIgnoreCase("primary")) {
            prefix = base;
        } else if (DOWNLOADS_DOCUMENTS.equals(provider) && root.equalsIgnoreCase("downloads")) {
            prefix = base + "/Download";
        } else if (DOWNLOADS_DOCUMENTS.equals(provider) && root.equals("raw")
                && (rel.equals(base) || rel.startsWith(base + "/"))) {
            prefix = base;
            rel = rel.substring(base.length());
        } else {
            throw new Rejected("Only folders in this user's internal shared storage can be shared with Linux");
        }
        List<String> out = new ArrayList<>();
        for (String seg : rel.split("/", -1)) {
            if (seg.isEmpty() || seg.equals(".")) continue;
            if (seg.equals("..") || containsControlChars(seg)) throw new Rejected("Invalid folder path");
            out.add(seg);
        }
        return out.isEmpty() ? prefix : prefix + "/" + String.join("/", out);
    }
}
