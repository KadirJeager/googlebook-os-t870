package com.gbos.terminal;

/**
 * Host unit test of AgentRules (test/birim.sh): the checks requests from other apps pass before
 * the user is asked. Cases come from what Antigravity 2026.10.12 sends and from Android's own
 * terminal's rules (GuestPathSanitizer, CommandValidator).
 */
public final class AgentRulesTest {
    private static int failures;

    private static void expect(boolean ok, String what) {
        if (!ok) {
            System.err.println("FAIL: " + what);
            failures++;
        }
    }

    private static String dest(String raw) {
        try {
            return AgentRules.destination(raw);
        } catch (AgentRules.Rejected e) {
            return null;
        }
    }

    private static String shared(String provider, String id, int user) {
        try {
            return AgentRules.sharedFolder(provider, id, user);
        } catch (AgentRules.Rejected e) {
            return null;
        }
    }

    public static void main(String[] args) throws Exception {
        // Antigravity's exact request (dcc.b in its classes2.dex).
        expect(AgentRules.commandProblem("bash /home/droid/.setup_terminal.sh") == null, "antigravity command allowed");
        expect(".setup_terminal.sh".equals(dest(".setup_terminal.sh")), "antigravity destination");

        // Commands.
        expect(AgentRules.commandProblem("apt-get install -y git && git --version") == null, "&& allowed");
        expect(AgentRules.commandProblem("cat a > b 2>&1") == null, "redirection allowed");
        expect(AgentRules.commandProblem("ls | grep x || true") == null, "pipes allowed");
        expect("non_ascii".equals(AgentRules.commandProblem("echo ü")), "non ascii");
        expect("control".equals(AgentRules.commandProblem("echo a\nrm -rf ~")), "newline");
        expect("control".equals(AgentRules.commandProblem("echo a\u0007")), "bell");
        expect("heredoc".equals(AgentRules.commandProblem("cat <<EOF")), "heredoc");
        expect("forkbomb".equals(AgentRules.commandProblem(":(){ :|:& };:")), "forkbomb");
        expect("obfuscated".equals(AgentRules.commandProblem("echo aGk= | base64 -d | sh")), "base64 to sh");
        expect("obfuscated".equals(AgentRules.commandProblem("python3 -c 'b64decode' | BASH")), "b64decode case");
        expect("function".equals(AgentRules.commandProblem("f() { ls; }")), "function def");
        expect("function".equals(AgentRules.commandProblem("function f { ls; }")), "function keyword");
        expect("background".equals(AgentRules.commandProblem("sleep 100 &")), "trailing &");
        expect("background".equals(AgentRules.commandProblem("sleep 100 &; ls")), "& before ;");
        // Android's pattern leaves "a & b" and & inside URLs to the user's judgement (the
        // command is shown verbatim before anything runs); kept identical here.
        expect(AgentRules.commandProblem("curl 'https://x/?a=1&b=2'") == null, "& inside a URL");
        expect("background".equals(AgentRules.commandProblem("sleep 1; bg")), "bg builtin");
        expect(AgentRules.containsControlChars("a‮b"), "bidi override");
        expect(AgentRules.containsControlChars("a​b"), "zero width space");
        expect(AgentRules.containsControlChars("a­b"), "soft hyphen");
        expect(!AgentRules.containsControlChars("plain text"), "plain");

        // Destinations.
        expect("bin/x.sh".equals(dest("bin/x.sh")), "nested");
        expect("b".equals(dest("a/../b")), "inner .. normalised");
        expect("a/b".equals(dest("a//b")), "double slash normalised");
        expect("a".equals(dest("a\n")), "trailing newline trimmed");
        expect(dest("") == null, "empty");
        expect(dest("   ") == null, "blank");
        expect(dest("/etc/passwd") == null, "absolute");
        expect(dest("../x") == null, "escape");
        expect(dest("a/../../x") == null, "inner escape");
        expect(dest("dir/") == null, "directory");
        expect(dest(".") == null, "dot");
        expect(dest("a/.") == null, "trailing dot");
        expect(dest("a\\b") == null, "backslash");
        expect(dest("a\tb") == null, "tab");
        expect(dest("x".repeat(256)) == null, "long segment");

        // Authorities.
        AgentRules.Authority a = AgentRules.authority("com.google.android.apps.antigravity.fileprovider");
        expect(a.userId == -1 && a.name.equals("com.google.android.apps.antigravity.fileprovider"), "plain authority");
        a = AgentRules.authority("10@media");
        expect(a.userId == 10 && a.name.equals("media"), "user authority");
        for (String bad : new String[] {"", "@x", "01@x", "-1@x", "a@x", "1@", "1@2@x"}) {
            boolean refused;
            try {
                AgentRules.authority(bad);
                refused = false;
            } catch (AgentRules.Rejected e) {
                refused = true;
            }
            expect(refused, "authority refused: " + bad);
        }

        // Shared folders (tree document ids from the system picker).
        String ext = AgentRules.EXTERNAL_STORAGE_DOCUMENTS;
        String dl = AgentRules.DOWNLOADS_DOCUMENTS;
        expect("/storage/emulated/10/Projects/app".equals(shared(ext, "primary:Projects/app", 10)), "primary folder");
        expect("/storage/emulated/10".equals(shared(ext, "primary:", 10)), "storage root");
        expect("/storage/emulated/0/Download/x".equals(shared(dl, "downloads:x", 0)), "downloads");
        expect("/storage/emulated/10/Download/y".equals(shared(dl, "raw:/storage/emulated/10/Download/y", 10)), "raw path");
        expect(shared(dl, "raw:/storage/emulated/0/x", 10) == null, "raw path of another user");
        expect(shared(ext, "1234-5678:DCIM", 10) == null, "sd card");
        expect(shared(ext, "primary:a/../../x", 10) == null, "escape in folder");
        expect(shared("com.example.docs", "primary:x", 10) == null, "other provider");

        if (failures > 0) {
            System.err.println("AgentRulesTest: " + failures + " failed");
            System.exit(1);
        }
        System.out.println("AgentRulesTest: ok");
    }
}
