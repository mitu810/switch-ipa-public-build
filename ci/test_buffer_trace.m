#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <stdatomic.h>
#include <stdarg.h>
#include <assert.h>
#include <unistd.h>
@protocol MTLBuffer <NSObject>
@property(readonly) uint64_t gpuAddress;
@property(readonly) NSUInteger length;
@end
static _Atomic uint64_t g_fence, g_done;
static unsigned attaches, destroys, events;
static void trace_log(const char *fmt, ...) {
    if (strstr(fmt, "[gpu-buf] A")) attaches++;
    else if (strstr(fmt, "[gpu-buf] D")) destroys++;
    else events++;
}
#define MLOG(...) trace_log(__VA_ARGS__)
#define GPU_BUF_TRACE_LOG(...) trace_log(__VA_ARGS__)
#include "nvn_gpu_buffer_trace.h"
@interface FakeGpuBuffer : NSObject <MTLBuffer> @end
@implementation FakeGpuBuffer
- (uint64_t)gpuAddress { return 0x1004904e000ull; }
- (NSUInteger)length { return 4096; }
@end
int main(void) { @autoreleasepool {
    __weak FakeGpuBuffer *weak;
    @autoreleasepool {
        FakeGpuBuffer *b = [FakeGpuBuffer new]; weak = b;
        gpu_buf_attach(b, 1); gpu_buf_event(b, "R+", 0);
        assert(!objc_getAssociatedObject(b, &g_gpu_buf_trace_key));
    }
    assert(!weak && !attaches && !destroys && !events);
    g_gpu_buf_trace_on = 1;
    @autoreleasepool {
        FakeGpuBuffer *b = [FakeGpuBuffer new]; weak = b;
        gpu_buf_attach(b, 12); gpu_buf_attach(b, 13);
        NvGpuBufferTrace *t = objc_getAssociatedObject(b, &g_gpu_buf_trace_key);
        assert(t && t->gpu_address == 0x1004904e000ull && t->bytes == 4096 && t->serial == 1);
        gpu_buf_event(b, "defer", 11); gpu_buf_event(b, "defer", 9);
        gpu_buf_retire(@[b], 12);
        assert(atomic_load(&t->last_retire) == 12);
        gpu_buf_event(b, "R-", 0);
        assert(attaches == 1 && events == 4 && destroys == 0);
    }
    assert(!weak && destroys == 1); // tracker must not keep the Metal buffer alive
    @autoreleasepool {
        FakeGpuBuffer *b = [FakeGpuBuffer new]; weak = b;
        gpu_buf_attach(b, 14);
        NvGpuBufferTrace *t = objc_getAssociatedObject(b, &g_gpu_buf_trace_key);
        assert(t->serial == 2); // same GPU range can belong to another allocation
    }
    assert(!weak && destroys == 2 && attaches == 2);
    char path[] = "/tmp/nx-gpu-trace-XXXXXX";
    int fd = mkstemp(path); assert(fd >= 0); close(fd);
    g_gpu_buf_trace_file = fopen(path, "w"); assert(g_gpu_buf_trace_file);
    setvbuf(g_gpu_buf_trace_file, NULL, _IOLBF, 0);
    gpu_buf_trace_log("[gpu-buf] test=%d", 7);
    FILE *check = fopen(path, "r"); assert(check); char row[256];
    assert(fgets(row, sizeof row, check) && strstr(row, "[gpu-buf] test=7"));
    fclose(check); fclose(g_gpu_buf_trace_file); g_gpu_buf_trace_file = NULL; unlink(path);
    puts("GPU buffer trace: disabled path, unique IDs, 64-bit address, maximum retirement fence and non-retaining destruction checks passed");
} return 0; }
