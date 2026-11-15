#import <UIKit/UIKit.h>
#import <Metal/Metal.h>
#include <math.h>
__attribute__((visibility("default"))) const int dm_glue_version=1;
__attribute__((visibility("default"))) const int dmgame_image_marker=0x4d50524f;
static NSMutableArray *results; static NSString *output;
static void save_report(NSString *status) {
    NSDictionary *r=@{@"status":status,@"device":MTLCreateSystemDefaultDevice().name?:@"none",@"cases":results?:@[],@"time":NSDate.date.description};
    NSData *d=[NSJSONSerialization dataWithJSONObject:r options:NSJSONWritingPrettyPrinted error:nil];
    [NSFileManager.defaultManager createDirectoryAtPath:output.stringByDeletingLastPathComponent withIntermediateDirectories:YES attributes:nil error:nil];
    [d writeToFile:output atomically:YES]; fprintf(stderr,"METAL_PROBE %s cases=%lu\n",status.UTF8String,(unsigned long)results.count);
}
static NSDictionary *one_case(id<MTLDevice> dev,id<MTLLibrary> lib,NSString *name,BOOL nested,BOOL explicitViews,BOOL indirect,BOOL classic,int storage,int residenceMode) {
    NSError *err=nil;
    MTLTextureDescriptor *td=[MTLTextureDescriptor new];td.textureType=MTLTextureType2DArray;td.width=td.height=8;td.arrayLength=4;td.mipmapLevelCount=3;td.pixelFormat=MTLPixelFormatRGBA8Unorm;td.storageMode=MTLStorageModeShared;td.usage=MTLTextureUsageShaderRead|MTLTextureUsagePixelFormatView;
    id<MTLBuffer> backing=nil;id<MTLHeap> heap=nil;id<MTLTexture> root=nil;
    if(storage==1){
        td.textureType=MTLTextureType2D;td.arrayLength=1;td.mipmapLevelCount=1;
        NSUInteger row=MAX(64ul,[dev minimumLinearTextureAlignmentForPixelFormat:td.pixelFormat]);backing=[dev newBufferWithLength:row*8 options:MTLResourceStorageModeShared];root=[backing newTextureWithDescriptor:td offset:0 bytesPerRow:row];
        uint8_t *px=backing.contents;for(NSUInteger y=0;y<8;y++)for(NSUInteger x=0;x<8;x++)for(int c=0;c<4;c++)px[y*row+x*4+c]=(uint8_t)(73+c);
    }else if(storage==2){
        td.storageMode=MTLStorageModePrivate;MTLSizeAndAlign n=[dev heapTextureSizeAndAlignWithDescriptor:td];MTLHeapDescriptor *hd=[MTLHeapDescriptor new];hd.type=MTLHeapTypePlacement;hd.storageMode=MTLStorageModePrivate;hd.size=n.size;heap=[dev newHeapWithDescriptor:hd];root=[heap newTextureWithDescriptor:td offset:0];
    }else root=[dev newTextureWithDescriptor:td];
    root.label=[name stringByAppendingString:@" root"];if(!root)return @{@"name":name,@"error":@"root allocation failed"};
    if(storage==0){for(NSUInteger lv=0;lv<3;lv++)for(NSUInteger layer=0;layer<4;layer++){
        NSUInteger w=8>>lv;uint8_t px[8*8*4];for(NSUInteger i=0;i<w*w;i++)for(int c=0;c<4;c++)px[4*i+c]=(uint8_t)(64*lv+4*layer+c+1);
        [root replaceRegion:MTLRegionMake2D(0,0,w,w) mipmapLevel:lv slice:layer withBytes:px bytesPerRow:w*4 bytesPerImage:w*w*4];
    }}else if(storage==2){
        id<MTLBuffer> upload=[dev newBufferWithLength:1024 options:MTLResourceStorageModeShared];uint8_t *p=upload.contents;for(int y=0;y<4;y++)for(int x=0;x<4;x++)for(int c=0;c<4;c++)p[y*256+x*4+c]=(uint8_t)(73+c);
        id<MTLCommandQueue> q=[dev newCommandQueue];id<MTLCommandBuffer> cb=[q commandBuffer];id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];[blit copyFromBuffer:upload sourceOffset:0 sourceBytesPerRow:256 sourceBytesPerImage:1024 sourceSize:MTLSizeMake(4,4,1) toTexture:root destinationSlice:2 destinationLevel:1 destinationOrigin:MTLOriginMake(0,0,0)];[blit endEncoding];[cb commit];[cb waitUntilCompleted];
    }
    NSUInteger lv=storage==1?0:1,slice=storage==1?0:2,count=storage==1?1:2;
    MTLTextureSwizzleChannels sw=MTLTextureSwizzleChannelsMake(MTLTextureSwizzleBlue,MTLTextureSwizzleGreen,MTLTextureSwizzleRed,MTLTextureSwizzleAlpha);
    id<MTLTexture> parent=[root newTextureViewWithPixelFormat:root.pixelFormat textureType:MTLTextureType2DArray levels:NSMakeRange(lv,count) slices:NSMakeRange(slice,storage==1?1:2) swizzle:sw];parent.label=[name stringByAppendingString:@" parent"];
    id<MTLTexture> view=nested?[parent newTextureViewWithPixelFormat:parent.pixelFormat textureType:MTLTextureType2D levels:NSMakeRange(0,count) slices:NSMakeRange(0,1) swizzle:sw]:[root newTextureViewWithPixelFormat:root.pixelFormat textureType:MTLTextureType2D levels:NSMakeRange(lv,count) slices:NSMakeRange(slice,1) swizzle:sw];view.label=[name stringByAppendingString:@" fitted"];
    if(!parent||!view)return @{@"name":name,@"error":@"view allocation failed"};
    id<MTLFunction> fn=[lib newFunctionWithName:indirect?@"read_indirect":@"read_direct"];
    MTLComputePipelineDescriptor *pd=[MTLComputePipelineDescriptor new];pd.computeFunction=fn;pd.label=name;id<MTLComputePipelineState> ps=[dev newComputePipelineStateWithDescriptor:pd options:MTLPipelineOptionNone reflection:nil error:&err];
    if(!ps)return @{@"name":name,@"error":err.localizedDescription?:@"pipeline failed"};
    id<MTLBuffer> out=[dev newBufferWithLength:16 options:MTLResourceStorageModeShared];memset(out.contents,0xcd,16);out.label=[name stringByAppendingString:@" output"];
    id<MTLArgumentEncoder> ae=indirect?[fn newArgumentEncoderWithBufferIndex:0]:nil;
    id<MTLBuffer> args=indirect?[dev newBufferWithLength:ae.encodedLength options:MTLResourceStorageModeShared]:nil;
    if(indirect){[ae setArgumentBuffer:args offset:0];[ae setTexture:view atIndex:0];[ae setBuffer:out offset:0 atIndex:1];}
    MTLResidencySetDescriptor *rd=[MTLResidencySetDescriptor new];rd.initialCapacity=8;
    id<MTLResidencySet> rs=[dev newResidencySetWithDescriptor:rd error:&err];if(residenceMode&1)[rs addAllocation:root];[rs addAllocation:out];if(args)[rs addAllocation:args];if(residenceMode&2){[rs addAllocation:parent];[rs addAllocation:view];}if(residenceMode&4){if(backing)[rs addAllocation:backing];if(heap)[rs addAllocation:heap];}[rs commit];
    __block NSString *gpuError=nil;
    if(classic){
        id<MTLCommandQueue> q=[dev newCommandQueue];[q addResidencySet:rs];id<MTLCommandBuffer> cb=[q commandBuffer];id<MTLComputeCommandEncoder> ce=[cb computeCommandEncoder];[ce setComputePipelineState:ps];
        if(indirect)[ce setBuffer:args offset:0 atIndex:0];else{[ce setTexture:view atIndex:0];[ce setBuffer:out offset:0 atIndex:0];}
        [ce dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];[ce endEncoding];[cb commit];[cb waitUntilCompleted];if(cb.error)gpuError=cb.error.description;
    }else{
        id<MTL4CommandQueue> q=[dev newMTL4CommandQueueWithDescriptor:[MTL4CommandQueueDescriptor new] error:&err];[q addResidencySet:rs];
        id<MTL4CommandAllocator> alloc=[dev newCommandAllocatorWithDescriptor:[MTL4CommandAllocatorDescriptor new] error:&err];id<MTL4CommandBuffer> cb=[dev newCommandBuffer];
        MTL4ArgumentTableDescriptor *ad=[MTL4ArgumentTableDescriptor new];ad.maxBufferBindCount=1;ad.maxTextureBindCount=1;id<MTL4ArgumentTable> at=[dev newArgumentTableWithDescriptor:ad error:&err];
        if(indirect)[at setAddress:args.gpuAddress atIndex:0];else{[at setTexture:view.gpuResourceID atIndex:0];[at setAddress:out.gpuAddress atIndex:0];}
        [cb beginCommandBufferWithAllocator:alloc];id<MTL4ComputeCommandEncoder> ce=[cb computeCommandEncoder];[ce setArgumentTable:at];[ce setComputePipelineState:ps];[ce dispatchThreadgroups:MTLSizeMake(1,1,1) threadsPerThreadgroup:MTLSizeMake(1,1,1)];[ce endEncoding];[cb endCommandBuffer];
        dispatch_semaphore_t done=dispatch_semaphore_create(0);MTL4CommitOptions *co=[MTL4CommitOptions new];[co addFeedbackHandler:^(id<MTL4CommitFeedback> f){if(f.error)gpuError=f.error.description;dispatch_semaphore_signal(done);}];id<MTL4CommandBuffer> bufs[]={cb};[q commit:bufs count:1 options:co];
        if(dispatch_semaphore_wait(done,dispatch_time(DISPATCH_TIME_NOW,15*NSEC_PER_SEC)))gpuError=@"GPU completion timed out";
    }
    const float *f=out.contents;NSMutableArray *values=[NSMutableArray array];for(int c=0;c<4;c++)[values addObject:isfinite(f[c])?@(f[c]):(id)@"nonfinite"];float want[]={75/255.f,74/255.f,73/255.f,76/255.f};BOOL equal=YES;for(int c=0;c<4;c++)if(!isfinite(f[c])||fabsf(f[c]-want[c])>0.0001f)equal=NO;
    NSMutableDictionary *r=[@{@"name":name,@"storage":@(storage),@"residence_mode":@(residenceMode),@"nested":@(nested),@"explicit_views":@(explicitViews),@"indirect":@(indirect),@"classic":@(classic),@"pixel_matches":@(equal),@"values":values,@"root_id":@(root.gpuResourceID._impl),@"parent_id":@(parent.gpuResourceID._impl),@"view_id":@(view.gpuResourceID._impl),@"view_parent_is_root":@(view.parentTexture==root)} mutableCopy];if(gpuError)r[@"gpu_error"]=gpuError;return r;
}
static void run_probe(void){@autoreleasepool{
    results=[NSMutableArray array];save_report(@"started");id<MTLDevice> dev=MTLCreateSystemDefaultDevice();if(![dev supportsFamily:MTLGPUFamilyMetal4]){save_report(@"no_metal4");return;}
    NSString *source=@"#include <metal_stdlib>\nusing namespace metal;\nstruct Args { texture2d<float> t [[id(0)]]; device float4 *out [[id(1)]]; };\nkernel void read_indirect(constant Args& a [[buffer(0)]]) { a.out[0]=a.t.read(uint2(0),0); }\nkernel void read_direct(texture2d<float> t [[texture(0)]],device float4* out [[buffer(0)]]) { out[0]=t.read(uint2(0),0); }\n";
    NSError *e=nil;id<MTLLibrary> lib=[dev newLibraryWithSource:source options:nil error:&e];if(!lib){[results addObject:@{@"error":e.localizedDescription?:@"library failed"}];save_report(@"compile_failed");return;}
    for(int indirect=0;indirect<2;indirect++)for(int nested=0;nested<2;nested++)for(int explicit=0;explicit<2;explicit++){
        NSString *n=[NSString stringWithFormat:@"m4_%@_%@_%@",indirect?@"indirect":@"direct",nested?@"nested":@"root",explicit?@"views_resident":@"root_resident"];
        NSDictionary *r=one_case(dev,lib,n,nested,explicit,indirect,NO,0,explicit?3:1);[results addObject:r];save_report(@"running");fprintf(stderr,"METAL_PROBE_CASE %s\n",[[r description] UTF8String]);
    }
    [results addObject:one_case(dev,lib,@"classic_root_direct",NO,NO,NO,YES,0,1)];for(int st=1;st<=2;st++)for(int ind=0;ind<2;ind++)for(int mode=4;mode<=7;mode++)if(mode!=6){
        NSString *n=[NSString stringWithFormat:@"%@_%@_res%d",st==1?@"buffer_backed":@"heap_backed",ind?@"indirect":@"direct",mode];
        @try{[results addObject:one_case(dev,lib,n,NO,(mode&2)!=0,ind,NO,st,mode)];}@catch(NSException *e){[results addObject:@{@"name":n,@"exception":e.reason?:@"unknown exception"}];}
        save_report(@"running");
    }save_report(@"complete");
}}
@interface NXMetalProbeVC:UIViewController @end
@implementation NXMetalProbeVC
- (void)viewDidLoad{[super viewDidLoad];self.view.backgroundColor=UIColor.systemBackgroundColor;UILabel*l=[[UILabel alloc]initWithFrame:self.view.bounds];l.autoresizingMask=UIViewAutoresizingFlexibleWidth|UIViewAutoresizingFlexibleHeight;l.textAlignment=NSTextAlignmentCenter;l.numberOfLines=0;l.text=@"Metal GPU diagnostics\nRunning a small synthetic test...";l.tag=17;[self.view addSubview:l];dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED,0),^{run_probe();dispatch_async(dispatch_get_main_queue(),^{((UILabel*)[self.view viewWithTag:17]).text=@"Metal GPU diagnostics finished\nReport saved for analysis";});});}
@end
__attribute__((visibility("default"))) const char *dm_glue_start_game(const char *const *argv,int argc,const void*image){(void)argv;(void)argc;(void)image;const char*p=getenv("DM_TEMP");output=[@(p?:NSTemporaryDirectory().UTF8String) stringByAppendingPathComponent:@"metal-probe.json"];return NULL;}
__attribute__((visibility("default"))) void dm_glue_present_game(void*window){((__bridge UIWindow*)window).rootViewController=[NXMetalProbeVC new];}
__attribute__((visibility("default"))) void dm_glue_focus_event(int e){(void)e;}
