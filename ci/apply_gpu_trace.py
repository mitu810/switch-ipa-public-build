"""Insert narrowly scoped optional observation hooks into the pinned Metal host."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

def apply(source, header):
    original = source.read_text()
    assert '#include "nvn_gpu_buffer_trace.h"' not in original, 'Diagnostic already installed'
    text = original
    def replace_once(old, new):
        nonlocal text
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    # The header needs the two counters; MLOG, Metal and Foundation are in scope.
    marker = next(line for line in text.splitlines() if line.startswith('static _Atomic uint64_t g_done;'))
    replace_once(marker, marker + '\n#include "nvn_gpu_buffer_trace.h"')
    replace_once('    g_res_check_on = getenv("DM_RES_CHECK") != NULL;',
                 '    g_res_check_on = getenv("DM_RES_CHECK") != NULL;\n'
                 '    { const char *t = getenv("DM_GPU_BUFFER_TRACE"); if (t && !strcmp(t, "1")) {\n'
                 '        if (gpu_buf_trace_begin()) MLOG("CPU-only GPU buffer lifetime trace enabled: gpu-buffer-lifetime.tsv");\n'
                 '        else MLOG("[RED] cannot open GPU buffer lifetime trace file");\n'
                 '    } }')
    replace_once('static void res_add_at(id<MTLAllocation> allocation, int line) { @autoreleasepool {\n    if (!allocation) return;',
                 'static void res_add_at(id<MTLAllocation> allocation, int line) { @autoreleasepool {\n    if (!allocation) return;\n    gpu_buf_attach((id)allocation, line);')
    replace_once('static void res_remove(id<MTLAllocation> r) { @autoreleasepool {\n    if (!r) return;',
                 'static void res_remove(id<MTLAllocation> r) { @autoreleasepool {\n    if (!r) return;\n    gpu_buf_event((id)r, "remove-request", 0);')
    replace_once('static void defer_obj_at(id obj, int keep_resident, uint64_t fence) {\n    if (!obj) return;',
                 'static void defer_obj_at(id obj, int keep_resident, uint64_t fence) {\n    if (!obj) return;\n    gpu_buf_retire(obj, fence);')
    assert text != original
    source.write_text(text)
    shutil.copy2(header, source.with_name(header.name))
    return {'source_before_sha256': hashlib.sha256(original.encode()).hexdigest(),
            'source_after_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'trace_header_sha256': hashlib.sha256(header.read_bytes()).hexdigest(),
            'hooks': ['initialization', 'buffer allocation', 'residency remove request', 'deferred retirement'],
            'gpu_commands_added': False, 'resource_retention_added': False, 'enabled_by_default': False}

if __name__ == '__main__':
    print(json.dumps(apply(Path(sys.argv[1]), Path(sys.argv[2])), indent=2))
