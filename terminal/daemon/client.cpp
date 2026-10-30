// gbos-linux: command-line client of gbos-linuxd. The Terminal app runs `gbos-linux shell` in
// each tab; root (the on-device updater, adb) uses `gbos-linux run` to execute commands in the
// Linux environment. See BENIOKU.md for the interface.
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

#include "config.h"
#include "util.h"

using namespace gbos;

namespace {

int g_sigpipe[2] = {-1, -1};
struct termios g_saved_tty;
bool g_tty_raw = false;

void restore_tty() {
    if (g_tty_raw) {
        tcsetattr(0, TCSAFLUSH, &g_saved_tty);
        g_tty_raw = false;
    }
}

void on_signal(int sig) {
    int saved = errno;
    unsigned char b = static_cast<unsigned char>(sig);
    if (write(g_sigpipe[1], &b, 1) < 0) {}
    errno = saved;
}

void usage(FILE* out) {
    fputs(
        "Usage: gbos-linux [COMMAND] [OPTIONS]\n"
        "\n"
        "  shell [--user NAME] [--cwd DIR]       login shell in the Linux environment (default)\n"
        "  run [OPTIONS] [-t] -- COMMAND [ARG]...  run a command; exit status is the command's\n"
        "      --user NAME     account to run as (root callers; default root)\n"
        "      --cwd DIR       working directory inside the environment\n"
        "      --env KEY=VALUE extra environment (repeatable)\n"
        "      --bind HOST:GUEST[:ro]  bind a host path for this command only (root)\n"
        "      -t              allocate a terminal (default: pass stdin/stdout/stderr through)\n"
        "  droid [-t] [--cwd DIR] [-- COMMAND [ARG]...]\n"
        "                                       as the droid account (the Terminal app only: the\n"
        "                                       user of Android's own Linux terminal, which apps\n"
        "                                       written for it log in as); no command: login shell\n"
        "  status [--json]                      installation and runtime state\n"
        "  install [--user NAME --android-user N] [--locale L]... [--repair]\n"
        "  cancel                               stop a running installation\n"
        "  useradd NAME --android-user N        Linux account for that user's Terminal\n"
        "  stop                                 end every process in the environment\n"
        "  remove --yes                         delete the environment and its homes\n"
        "\n"
        "Exit status: the command's own, 128+N if killed by signal N, 69 if the environment is not\n"
        "installed, 125 for errors of gbos-linux itself.\n",
        out);
}

int connect_daemon(Channel* ch) {
    Paths paths = Paths::get();
    int fd = connect_unix(paths.socket);
    if (fd < 0) {
        fprintf(stderr, "gbos-linux: cannot reach the Linux service (%s): %s\n",
                paths.socket.c_str(), strerror(errno));
        return -1;
    }
    *ch = Channel(fd);
    return 0;
}

int error_exit(const json& reply) {
    std::string code = reply.value("code", "");
    fprintf(stderr, "gbos-linux: %s\n", reply.value("error", std::string("request failed")).c_str());
    if (code == "exec_failed") return reply.value("status", kExitToolError);
    return code == "not_installed" ? kExitNotInstalled : kExitToolError;
}

int simple(const json& request) {
    Channel ch;
    if (connect_daemon(&ch) != 0) return kExitToolError;
    json reply;
    if (!ch.send(request) || !ch.recv(&reply)) {
        fprintf(stderr, "gbos-linux: lost the connection to the service\n");
        return kExitToolError;
    }
    if (!reply.value("ok", false)) return error_exit(reply);
    return 0;
}

int cmd_status(bool as_json) {
    Channel ch;
    if (connect_daemon(&ch) != 0) return kExitToolError;
    json reply;
    if (!ch.send({{"op", "status"}}) || !ch.recv(&reply)) return kExitToolError;
    if (!reply.value("ok", false)) return error_exit(reply);
    if (as_json) {
        printf("%s\n", reply.dump(2).c_str());
        return 0;
    }
    for (auto& [k, v] : reply.items()) {
        if (k == "ok") continue;
        printf("%s=%s\n", k.c_str(), v.is_string() ? v.get<std::string>().c_str() : v.dump().c_str());
    }
    return 0;
}

int cmd_install(json request) {
    Channel ch;
    if (connect_daemon(&ch) != 0) return kExitToolError;
    if (!ch.send(request)) return kExitToolError;
    json ev;
    bool tty = isatty(1);
    while (ch.recv(&ev)) {
        std::string kind = ev.value("event", "");
        if (kind == "progress") {
            std::string detail = ev.value("step", "");
            if (ev.contains("bytes"))
                detail = format("%.1f/%.1f MB", ev["bytes"].get<double>() / 1e6,
                                ev["total"].get<double>() / 1e6);
            if (tty) fprintf(stdout, "\r\033[K[%5.1f%%] %s %s", ev.value("percent", 0.0),
                             ev.value("phase", "").c_str(), detail.c_str());
            else fprintf(stdout, "[%5.1f%%] %s %s\n", ev.value("percent", 0.0),
                         ev.value("phase", "").c_str(), detail.c_str());
            fflush(stdout);
        } else if (kind == "log") {
            if (tty) fputs("\r\033[K", stdout);
            printf("%s\n", ev.value("line", "").c_str());
        } else if (kind == "done") {
            if (tty) fputs("\r\033[K", stdout);
            if (ev.value("ok", false)) {
                printf("installed\n");
                return 0;
            }
            fprintf(stderr, "gbos-linux: installation failed (%s): %s\n",
                    ev.value("phase", "").c_str(), ev.value("error", "").c_str());
            return kExitToolError;
        } else if (ev.contains("ok") && !ev.value("ok", false)) {
            return error_exit(ev);
        }
    }
    fprintf(stderr, "gbos-linux: lost the connection to the service\n");
    return kExitToolError;
}

void install_signal_handlers(bool forward_job_signals) {
    if (pipe2(g_sigpipe, O_CLOEXEC | O_NONBLOCK) != 0) return;
    struct sigaction sa = {};
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGWINCH, &sa, nullptr);
    sigaction(SIGTERM, &sa, nullptr);
    sigaction(SIGHUP, &sa, nullptr);
    if (forward_job_signals) {
        sigaction(SIGINT, &sa, nullptr);
        sigaction(SIGQUIT, &sa, nullptr);
        sigaction(SIGUSR1, &sa, nullptr);
        sigaction(SIGUSR2, &sa, nullptr);
    }
    signal(SIGPIPE, SIG_IGN);
}

bool write_fully(int fd, const char* p, size_t n) {
    while (n > 0) {
        ssize_t w = TEMP_FAILURE_RETRY(write(fd, p, n));
        if (w < 0 && errno == EAGAIN) {
            pollfd pfd = {fd, POLLOUT, 0};
            poll(&pfd, 1, -1);
            continue;
        }
        if (w <= 0) return false;
        p += w;
        n -= w;
    }
    return true;
}

int exit_status_of(const json& ev) { return ev.value("status", kExitToolError); }

// Interactive session: the daemon hands over the master side of a pty inside the environment;
// this process relays between it and its own terminal (the Terminal app's view, adb, ...).
int session_tty(json request) {
    struct winsize ws = {};
    if (ioctl(0, TIOCGWINSZ, &ws) == 0 && ws.ws_row && ws.ws_col) {
        request["rows"] = ws.ws_row;
        request["cols"] = ws.ws_col;
    }
    Channel ch;
    if (connect_daemon(&ch) != 0) return kExitToolError;
    install_signal_handlers(false);
    json reply;
    std::vector<int> fds;
    if (!ch.send(request) || !ch.recv(&reply, &fds)) {
        fprintf(stderr, "gbos-linux: lost the connection to the service\n");
        return kExitToolError;
    }
    if (!reply.value("ok", false) || fds.size() != 1) {
        for (int f : fds) close(f);
        return error_exit(reply);
    }
    int master = fds[0];
    if (ws.ws_row && ws.ws_col) ioctl(master, TIOCSWINSZ, &ws);
    if (isatty(0) && tcgetattr(0, &g_saved_tty) == 0) {
        struct termios raw = g_saved_tty;
        cfmakeraw(&raw);
        if (tcsetattr(0, TCSAFLUSH, &raw) == 0) {
            g_tty_raw = true;
            atexit(restore_tty);
        }
    }
    bool stdin_open = true, master_open = true;
    bool have_exit = false;
    json exit_ev;
    char buf[16384];
    while (master_open || !have_exit) {
        pollfd p[4] = {{stdin_open && master_open ? 0 : -1, POLLIN, 0},
                       {master_open ? master : -1, POLLIN, 0},
                       {ch.fd(), POLLIN, 0},
                       {g_sigpipe[0], POLLIN, 0}};
        // After the exit report, only drain what the session still printed.
        int timeout = have_exit ? 200 : -1;
        int n = poll(p, 4, timeout);
        if (n < 0 && errno != EINTR) break;
        if (n == 0 && have_exit) break;
        if (p[3].revents & POLLIN) {
            unsigned char sig;
            while (read(g_sigpipe[0], &sig, 1) == 1) {
                if (sig == SIGWINCH) {
                    if (ioctl(0, TIOCGWINSZ, &ws) == 0) ioctl(master, TIOCSWINSZ, &ws);
                } else {
                    restore_tty();
                    _exit(128 + sig);  // closing the master hangs up the remote session
                }
            }
        }
        if (p[1].revents & (POLLIN | POLLHUP | POLLERR)) {
            ssize_t r = read(master, buf, sizeof buf);
            if (r > 0) {
                if (!write_fully(1, buf, r)) master_open = false;
            } else if (r == 0 || (errno != EAGAIN && errno != EINTR)) {
                master_open = false;  // EIO: every process of the session closed the terminal
            }
        }
        if (p[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            ssize_t r = read(0, buf, sizeof buf);
            if (r > 0) write_fully(master, buf, r);
            else if (r == 0 || (errno != EAGAIN && errno != EINTR)) stdin_open = false;
        }
        if (p[2].revents & (POLLIN | POLLHUP | POLLERR)) {
            json ev;
            if (ch.recv(&ev)) {
                if (ev.value("event", "") == "exit") {
                    exit_ev = ev;
                    have_exit = true;
                }
            } else {
                if (!have_exit) {
                    restore_tty();
                    fprintf(stderr, "\r\ngbos-linux: the Linux service ended the session\r\n");
                    return kExitToolError;
                }
                break;
            }
        }
    }
    close(master);
    restore_tty();
    return have_exit ? exit_status_of(exit_ev) : kExitToolError;
}

// Non-interactive: the command gets this process's own stdin/stdout/stderr; signals are
// forwarded to its process group.
int session_pipe(json request) {
    Channel ch;
    if (connect_daemon(&ch) != 0) return kExitToolError;
    install_signal_handlers(true);
    json reply;
    if (!ch.send(request, {0, 1, 2}) || !ch.recv(&reply)) {
        fprintf(stderr, "gbos-linux: lost the connection to the service\n");
        return kExitToolError;
    }
    if (!reply.value("ok", false)) return error_exit(reply);
    // The command has its own copies; keep ours from holding pipes open needlessly.
    int devnull = open("/dev/null", O_RDWR);
    dup2(devnull, 0);
    while (true) {
        pollfd p[2] = {{ch.fd(), POLLIN, 0}, {g_sigpipe[0], POLLIN, 0}};
        if (poll(p, 2, -1) < 0 && errno != EINTR) break;
        if (p[1].revents & POLLIN) {
            unsigned char sig;
            while (read(g_sigpipe[0], &sig, 1) == 1) ch.send({{"op", "signal"}, {"sig", sig}});
        }
        if (p[0].revents) {
            json ev;
            if (!ch.recv(&ev)) break;
            if (ev.value("event", "") == "exit") return exit_status_of(ev);
        }
    }
    fprintf(stderr, "gbos-linux: the Linux service ended the session\n");
    return kExitToolError;
}

json session_env(bool tty) {
    json env = json::array();
    if (tty) {
        for (const char* k : {"TERM", "COLORTERM", "LANG", "TERM_PROGRAM", "TERM_PROGRAM_VERSION"}) {
            if (const char* v = getenv(k)) env.push_back(std::string(k) + "=" + v);
        }
        for (char** e = environ; *e; e++) {
            if (strncmp(*e, "LC_", 3) == 0) env.push_back(*e);
        }
    }
    return env;
}

}  // namespace

int main(int argc, char** argv) {
    // A caller may start us with a standard stream closed (adb shell without a pty, daemons):
    // /dev/null takes its place, so a command always gets three streams and fd numbers stay put.
    for (int fd = 0; fd < 3; fd++) {
        if (fcntl(fd, F_GETFD) < 0) {
            int n = open("/dev/null", O_RDWR);
            if (n >= 0 && n != fd) {
                dup2(n, fd);
                close(n);
            }
        }
    }
    log_init("gbos-linux", true);
    std::string command = "shell";
    int first = 1;
    if (argc > 1 && argv[1][0] != '-') {
        command = argv[1];
        first = 2;
    } else if (argc > 1 && (!strcmp(argv[1], "-h") || !strcmp(argv[1], "--help"))) {
        usage(stdout);
        return 0;
    }
    // Options shared by the commands.
    std::string user, cwd;
    json env = json::array(), binds = json::array(), locales = json::array();
    bool force_tty = false, as_json = false, repair = false, yes = false;
    int android_user = -1;
    static const option opts[] = {
        {"user", required_argument, nullptr, 'u'}, {"cwd", required_argument, nullptr, 'C'},
        {"env", required_argument, nullptr, 'e'},  {"bind", required_argument, nullptr, 'b'},
        {"json", no_argument, nullptr, 'j'},       {"repair", no_argument, nullptr, 'r'},
        {"locale", required_argument, nullptr, 'l'}, {"android-user", required_argument, nullptr, 'a'},
        {"yes", no_argument, nullptr, 'y'},        {"help", no_argument, nullptr, 'h'},
        {nullptr, 0, nullptr, 0}};
    optind = first;
    int c;
    // run/shell: '+' stops at the first non-option, so `run cmd -x` keeps -x for the command;
    // the other commands take their options anywhere (`useradd NAME --android-user N`).
    bool passthrough = command == "run" || command == "shell" || command == "droid";
    const char* optstring = passthrough ? "+tu:C:e:b:h" : "tu:C:e:b:h";
    while ((c = getopt_long(argc, argv, optstring, opts, nullptr)) != -1) {
        switch (c) {
            case 't': force_tty = true; break;
            case 'u': user = optarg; break;
            case 'C': cwd = optarg; break;
            case 'e': env.push_back(optarg); break;
            case 'b': binds.push_back(optarg); break;
            case 'j': as_json = true; break;
            case 'r': repair = true; break;
            case 'l': locales.push_back(optarg); break;
            case 'a': android_user = atoi(optarg); break;
            case 'y': yes = true; break;
            case 'h': usage(stdout); return 0;
            default: usage(stderr); return kExitToolError;
        }
    }
    std::vector<std::string> rest(argv + optind, argv + argc);

    if (command == "status") return cmd_status(as_json);
    if (command == "install") {
        json req = {{"op", "install"}, {"repair", repair}, {"locales", locales}};
        if (!user.empty()) {
            req["user"] = user;
            req["android_user"] = android_user;
        }
        return cmd_install(req);
    }
    if (command == "cancel") return simple({{"op", "cancel"}});
    if (command == "stop") return simple({{"op", "stop"}});
    if (command == "useradd") {
        if (rest.size() != 1) {
            usage(stderr);
            return kExitToolError;
        }
        return simple({{"op", "useradd"}, {"name", rest[0]}, {"android_user", android_user}});
    }
    if (command == "remove") {
        if (!yes) {
            fprintf(stderr, "gbos-linux: remove deletes the environment and every Linux home; "
                            "add --yes to confirm\n");
            return kExitToolError;
        }
        return simple({{"op", "remove"}});
    }
    if (command != "shell" && command != "run" && command != "droid") {
        fprintf(stderr, "gbos-linux: unknown command '%s'\n", command.c_str());
        usage(stderr);
        return kExitToolError;
    }
    if (command == "run" && rest.empty()) {
        fprintf(stderr, "gbos-linux: run needs a command\n");
        return kExitToolError;
    }
    bool interactive = command == "shell" || (command == "droid" && rest.empty());
    bool tty = interactive ? isatty(0) && isatty(1) : force_tty;
    json req = {{"op", "spawn"}, {"tty", tty}};
    if (command != "shell" && !rest.empty()) req["argv"] = rest;
    if (command == "droid") {
        if (!user.empty()) {
            fprintf(stderr, "gbos-linux: droid runs as the droid account\n");
            return kExitToolError;
        }
        req["droid"] = true;
    }
    if (!user.empty()) req["user"] = user;
    if (!cwd.empty()) req["cwd"] = cwd;
    if (android_user >= 0) req["android_user"] = android_user;
    json e = session_env(tty);
    for (auto& x : env) e.push_back(x);
    req["env"] = e;
    if (!binds.empty()) req["binds"] = binds;
    return tty ? session_tty(req) : session_pipe(req);
}
