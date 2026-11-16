// Optional CPU-only buffer lifetime observation. No GPU commands, resource
// retention, binding-cache changes or waits. This is a diagnostic, not a fix.
#include <objc/runtime.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <time.h>
static int g_gpu_buf_trace_on;
static char g_gpu_buf_trace_key;
static _Atomic uint64_t g_gpu_buf_trace_seq;
static pthread_mutex_t g_gpu_buf_trace_mu = PTHREAD_MUTEX_INITIALIZER;
static FILE *g_gpu_buf_trace_file;
// A dedicated line-buffered file avoids the main log tee's per-line fsync.
// Each complete event reaches the kernel without a flush of the whole disk.
static void gpu_buf_trace_log(const char *fmt, ...) {
    FILE *f = g_gpu_buf_trace_file;
    if (!f) return;
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    flockfile(f);
    fprintf(f, "%llu\t", (unsigned long long)t.tv_sec * 1000000000ull + t.tv_nsec);
    va_list args; va_start(args, fmt); vfprintf(f, fmt, args); va_end(args);
    fputc('\n', f);
    funlockfile(f);
}
#ifndef GPU_BUF_TRACE_LOG
#define GPU_BUF_TRACE_LOG(...) gpu_buf_trace_log(__VA_ARGS__)
#endif
// Called before Metal initialization and worker creation. Keep one prior run.
static int gpu_buf_trace_begin(void) {
    if (g_gpu_buf_trace_on) return 1;
    const char *name = "gpu-buffer-lifetime.tsv";
    rename(name, "gpu-buffer-lifetime.prev.tsv");
    g_gpu_buf_trace_file = fopen(name, "w");
    if (!g_gpu_buf_trace_file) return 0;
    setvbuf(g_gpu_buf_trace_file, NULL, _IOLBF, 0);
    g_gpu_buf_trace_on = 1;
    gpu_buf_trace_log("[gpu-buf] schema=1 monotonic_ns events=allocation,remove-request,retire,destroy");
    return 1;
}
@interface NvGpuBufferTrace : NSObject {
@public
    uint64_t serial, object_address, gpu_address, bytes;
    _Atomic uint64_t last_retire;
}
@end
@implementation NvGpuBufferTrace
- (void)dealloc {
    GPU_BUF_TRACE_LOG("[gpu-buf] D id=%llu obj=%#llx base=%#llx len=%llu retire=%llu commit=%llu done=%llu",
        (unsigned long long)serial, (unsigned long long)object_address, (unsigned long long)gpu_address,
        (unsigned long long)bytes, (unsigned long long)atomic_load(&last_retire),
        (unsigned long long)atomic_load(&g_fence), (unsigned long long)atomic_load(&g_done));
}
@end
static void gpu_buf_attach(id object, int line) {
    if (!g_gpu_buf_trace_on || !object || ![object conformsToProtocol:@protocol(MTLBuffer)]) return;
    pthread_mutex_lock(&g_gpu_buf_trace_mu);
    if (objc_getAssociatedObject(object, &g_gpu_buf_trace_key)) { pthread_mutex_unlock(&g_gpu_buf_trace_mu); return; }
    id<MTLBuffer> b = object;
    NvGpuBufferTrace *t = [NvGpuBufferTrace new];
    t->serial = atomic_fetch_add(&g_gpu_buf_trace_seq, 1) + 1;
    t->object_address = (uint64_t)(uintptr_t)(__bridge void *)object;
    t->gpu_address = b.gpuAddress; t->bytes = b.length;
    objc_setAssociatedObject(object, &g_gpu_buf_trace_key, t, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    pthread_mutex_unlock(&g_gpu_buf_trace_mu);
    GPU_BUF_TRACE_LOG("[gpu-buf] A id=%llu obj=%#llx base=%#llx len=%llu line=%d commit=%llu done=%llu",
        (unsigned long long)t->serial, (unsigned long long)t->object_address,
        (unsigned long long)t->gpu_address, (unsigned long long)t->bytes, line,
        (unsigned long long)atomic_load(&g_fence), (unsigned long long)atomic_load(&g_done));
}
static void gpu_buf_event(id object, const char *op, uint64_t retire) {
    if (!g_gpu_buf_trace_on || !object) return;
    NvGpuBufferTrace *t = objc_getAssociatedObject(object, &g_gpu_buf_trace_key);
    if (!t) return;
    if (retire) {
        uint64_t old = atomic_load(&t->last_retire);
        while (retire > old && !atomic_compare_exchange_weak(&t->last_retire, &old, retire)) {}
    }
    GPU_BUF_TRACE_LOG("[gpu-buf] %s id=%llu retire=%llu commit=%llu done=%llu", op,
        (unsigned long long)t->serial, (unsigned long long)retire,
        (unsigned long long)atomic_load(&g_fence), (unsigned long long)atomic_load(&g_done));
}
static void gpu_buf_retire(id object, uint64_t fence) {
    if (!g_gpu_buf_trace_on || !object) return;
    if ([object isKindOfClass:NSArray.class]) {
        for (id child in (NSArray *)object) gpu_buf_retire(child, fence);
    } else gpu_buf_event(object, "retire", fence);
}
