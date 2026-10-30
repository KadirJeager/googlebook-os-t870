package com.gbos.terminal;

import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * HTTPS client for gbos-linuxd, run as root through app_process with this APK on the class path:
 *
 *   CLASSPATH=Terminal.apk app_process64 /system/bin com.gbos.terminal.Fetch \
 *       [-H "Name: value"]... [--auth FILE] [--resume] [-o FILE] URL
 *
 * Android's own HTTPS stack (system CA store, Android's resolver) instead of a command-line tool
 * the image may or may not ship. --auth names a file of header lines that are sent only to the
 * URL's own host: when a registry redirects a blob to its CDN, the token stays behind.
 * Exit status: 0 ok, 2 usage, 3 HTTP error, 4 network/IO error.
 */
public final class Fetch {
    private static final int MAX_REDIRECTS = 8;
    private static final int ATTEMPTS = 5;

    public static void main(String[] args) {
        List<String[]> headers = new ArrayList<>();
        List<String[]> authHeaders = new ArrayList<>();
        String output = null, url = null;
        boolean resume = false;
        try {
            for (int i = 0; i < args.length; i++) {
                switch (args[i]) {
                    case "-H": headers.add(header(args[++i])); break;
                    case "--auth": authHeaders.addAll(readHeaders(args[++i])); break;
                    case "--resume": resume = true; break;
                    case "-o": output = args[++i]; break;
                    default: url = args[i];
                }
            }
        } catch (RuntimeException | IOException e) {
            System.err.println("usage error: " + e.getMessage());
            System.exit(2);
        }
        if (url == null || !url.startsWith("https://")) {
            System.err.println("an https:// URL is required");
            System.exit(2);
        }
        int status = 4;
        for (int attempt = 1; attempt <= ATTEMPTS; attempt++) {
            try {
                status = fetch(url, headers, authHeaders, output, resume || attempt > 1);
                break;
            } catch (HttpError e) {
                System.err.println(e.getMessage());
                status = 3;
                if (e.code < 500 && e.code != 429) break;  // not worth retrying
            } catch (IOException e) {
                System.err.println(url + ": " + e);
                status = 4;
            }
            try {
                Thread.sleep(2000L * attempt);
            } catch (InterruptedException ignored) {
                break;
            }
        }
        System.exit(status);
    }

    private static final class HttpError extends IOException {
        final int code;

        HttpError(int code, String message) {
            super(message);
            this.code = code;
        }
    }

    private static String[] header(String line) {
        int colon = line.indexOf(':');
        if (colon <= 0) throw new IllegalArgumentException("bad header " + line);
        return new String[] {line.substring(0, colon).trim(), line.substring(colon + 1).trim()};
    }

    private static List<String[]> readHeaders(String file) throws IOException {
        List<String[]> out = new ArrayList<>();
        try (InputStream in = new FileInputStream(file)) {
            for (String line : new String(in.readAllBytes(), StandardCharsets.UTF_8).split("\n")) {
                if (!line.trim().isEmpty()) out.add(header(line));
            }
        }
        return out;
    }

    private static int fetch(String start, List<String[]> headers, List<String[]> authHeaders,
                             String output, boolean resume) throws IOException {
        long have = 0;
        if (output != null && resume) {
            java.io.File f = new java.io.File(output);
            if (f.isFile()) have = f.length();
        }
        URL first = new URL(start);
        URL url = first;
        for (int hop = 0; hop <= MAX_REDIRECTS; hop++) {
            HttpURLConnection c = (HttpURLConnection) url.openConnection();
            c.setInstanceFollowRedirects(false);
            c.setConnectTimeout(30_000);
            c.setReadTimeout(60_000);
            c.setRequestProperty("User-Agent", "gbos-linux/1 (Googlebook Linux)");
            for (String[] h : headers) c.addRequestProperty(h[0], h[1]);
            if (url.getHost().equalsIgnoreCase(first.getHost())) {
                for (String[] h : authHeaders) c.addRequestProperty(h[0], h[1]);
            }
            if (have > 0) c.setRequestProperty("Range", "bytes=" + have + "-");
            int code = c.getResponseCode();
            if (code >= 300 && code < 400 && code != 304) {
                String location = c.getHeaderField("Location");
                c.disconnect();
                if (location == null) throw new HttpError(code, url + ": redirect without a location");
                url = new URL(url, location);
                if (!"https".equals(url.getProtocol()))
                    throw new HttpError(code, "refusing a redirect to " + url.getProtocol());
                continue;
            }
            if (code == 416 && have > 0) {  // already complete
                c.disconnect();
                return 0;
            }
            if (code != 200 && code != 206) {
                String body = "";
                try (InputStream err = c.getErrorStream()) {
                    if (err != null) body = new String(err.readNBytes(512), StandardCharsets.UTF_8);
                } catch (IOException ignored) {
                }
                c.disconnect();
                throw new HttpError(code, url.getHost() + url.getPath() + ": HTTP " + code
                        + (body.isEmpty() ? "" : " " + body.replace('\n', ' ')));
            }
            boolean append = code == 206 && have > 0;
            try (InputStream in = c.getInputStream();
                 OutputStream out = output == null ? System.out : new FileOutputStream(output, append)) {
                byte[] buf = new byte[256 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                out.flush();
            } finally {
                c.disconnect();
            }
            return 0;
        }
        throw new HttpError(310, start + ": too many redirects");
    }
}
