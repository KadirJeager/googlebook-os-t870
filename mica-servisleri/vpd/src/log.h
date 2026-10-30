// Logging that goes to logcat on the device and to stderr in the host unit tests.
#pragma once

#ifdef __ANDROID__
#include <android/log.h>
#define VPD_LOG(prio, ...) __android_log_print(ANDROID_LOG_##prio, "gbos-vpd-executor", __VA_ARGS__)
#else
#include <cstdio>
#define VPD_LOG(prio, ...) (std::fprintf(stderr, #prio " gbos-vpd-executor: " __VA_ARGS__), \
                            std::fputc('\n', stderr))
#endif

#define LOGE(...) VPD_LOG(ERROR, __VA_ARGS__)
#define LOGW(...) VPD_LOG(WARN, __VA_ARGS__)
#define LOGI(...) VPD_LOG(INFO, __VA_ARGS__)
