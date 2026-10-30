package com.gbos.terminal;

import android.net.LocalSocket;
import android.net.LocalSocketAddress;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.Closeable;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

/**
 * Connection to gbos-linuxd (/dev/socket/gbos_linux): one JSON object per line in each
 * direction. The service identifies this app by its uid; see terminal/BENIOKU.md.
 */
final class DaemonClient implements Closeable {
    static final String SOCKET = "/dev/socket/gbos_linux";

    private final LocalSocket socket;
    private final BufferedReader in;
    private final OutputStream out;

    private DaemonClient(LocalSocket socket) throws IOException {
        this.socket = socket;
        this.in = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
        this.out = socket.getOutputStream();
    }

    static DaemonClient connect() throws IOException {
        LocalSocket s = new LocalSocket();
        try {
            s.connect(new LocalSocketAddress(SOCKET, LocalSocketAddress.Namespace.FILESYSTEM));
        } catch (IOException e) {
            s.close();
            throw e;
        }
        return new DaemonClient(s);
    }

    void send(JSONObject message) throws IOException {
        out.write((message.toString() + "\n").getBytes(StandardCharsets.UTF_8));
        out.flush();
    }

    /** Next message, or null when the service closed the connection. */
    JSONObject read() throws IOException {
        String line = in.readLine();
        if (line == null) return null;
        try {
            return new JSONObject(line);
        } catch (JSONException e) {
            throw new IOException("invalid message from the Linux service", e);
        }
    }

    /** Sends one request and returns its reply. */
    static JSONObject request(JSONObject message) throws IOException {
        try (DaemonClient c = connect()) {
            c.send(message);
            JSONObject reply = c.read();
            if (reply == null) throw new IOException("the Linux service closed the connection");
            return reply;
        }
    }

    static JSONObject op(String name) {
        try {
            return new JSONObject().put("op", name);
        } catch (JSONException e) {
            throw new AssertionError(e);
        }
    }

    @Override
    public void close() throws IOException {
        socket.close();
    }
}
