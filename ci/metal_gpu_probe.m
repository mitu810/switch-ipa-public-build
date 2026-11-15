#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
int main(void) { @autoreleasepool {
    id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
    NSMutableDictionary *r = [NSMutableDictionary dictionary];
    r[@"os"] = NSProcessInfo.processInfo.operatingSystemVersionString;
    r[@"device"] = dev.name ?: @"none";
    r[@"unified_memory"] = @(dev && dev.hasUnifiedMemory);
    BOOL family = dev && [dev supportsFamily:MTLGPUFamilyMetal4];
    r[@"metal4_family"] = @(family);
    NSError *err = nil;
    id<MTLResidencySet> rs = family ? [dev newResidencySetWithDescriptor:[MTLResidencySetDescriptor new] error:&err] : nil;
    id<MTL4CommandQueue> q = family ? [dev newMTL4CommandQueueWithDescriptor:[MTL4CommandQueueDescriptor new] error:&err] : nil;
    r[@"residency_set"] = @(rs != nil); r[@"metal4_queue"] = @(q != nil);
    r[@"gpu_tests_runnable"] = @(family && rs && q);
    if (err) r[@"error"] = err.localizedDescription;
    NSData *json = [NSJSONSerialization dataWithJSONObject:r options:NSJSONWritingPrettyPrinted error:nil];
    fwrite(json.bytes, 1, json.length, stdout); fputc('\n', stdout);
    return 0;
} }
