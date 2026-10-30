package com.gbos.terminal;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/**
 * Commands the user approved in AgentRequestActivity, handed to the window or service that runs
 * them by a one-time token. Process-local on purpose: an intent extra of the exported Terminal
 * window could be sent by any app, a token from this map only by this process.
 */
final class DroidRequests {
    /** An approved command and the app that asked for it. */
    static final class Approved {
        final String command;
        final String callerLabel;

        Approved(String command, String callerLabel) {
            this.command = command;
            this.callerLabel = callerLabel;
        }
    }

    private static final Map<String, Approved> PENDING = new HashMap<>();

    private DroidRequests() {
    }

    static synchronized String put(Approved approved) {
        String token = UUID.randomUUID().toString();
        PENDING.put(token, approved);
        return token;
    }

    /** The approved command for a token, once; null for an unknown or used token. */
    static synchronized Approved take(String token) {
        return token == null ? null : PENDING.remove(token);
    }
}
