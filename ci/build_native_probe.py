"""Package a synthetic iOS framework for the existing NX2Apple shell ABI; no game data."""
import json,plistlib,subprocess,datetime,zipfile
from pathlib import Path
T='F000000000000001';N='DmGame'+T;d=Path('probe-output')/(N+'.framework');d.mkdir(parents=True)
sdk=subprocess.check_output(['xcrun','--sdk','iphoneos','--show-sdk-path'],text=True).strip()
subprocess.run(['xcrun','--sdk','iphoneos','clang','-arch','arm64','-isysroot',sdk,'-miphoneos-version-min=26.0','-fobjc-arc','-fblocks','-O1','-gline-tables-only','-dynamiclib','-install_name','@rpath/'+N+'.framework/'+N,'-framework','Foundation','-framework','UIKit','-framework','Metal','ci/MetalProbe.m','-o',str(d/N)],check=True)
info={'CFBundleIdentifier':'org.nxnative.metalprobe','CFBundleExecutable':N,'CFBundleName':N,'CFBundlePackageType':'FMWK','CFBundleVersion':'1','CFBundleShortVersionString':'1.0','MinimumOSVersion':'26.0','CFBundleSupportedPlatforms':['iPhoneOS']};(d/'Info.plist').write_bytes(plistlib.dumps(info))
m={'schema':1,'title_id':T,'name':'Metal GPU diagnostics (temporary)','display_version':'1.0','package_version':'synthetic-test','modules':['probe'],'nro_count':0,'chain':'b-a5e7fb6e4fa4','loader':'FH','framework':N+'.framework','framework_executable':N,'image_marker':'dmgame_image_marker','profile':'profile.tsv','shader_map':'dm/shader_map.tsv','launcher':'dm/launcher.json','romfs_marker':'probe.marker','romfs_identity':'','min_os':'26.0','console':'switch1','built_at':datetime.datetime.now(datetime.timezone.utc).isoformat()};(d/'game.manifest.json').write_text(json.dumps(m,indent=2)+'\n');(d/'profile.tsv').write_text('title_id\t'+T+'\nname\tMetal GPU diagnostics\n');(d/'dm').mkdir();(d/'dm/shader_map.tsv').write_text('# Synthetic Metal diagnostics, no game shaders\n');(d/'dm/launcher.json').write_text(json.dumps({'schema':1,'game':T,'name':'Metal GPU diagnostics','settings':[]})+'\n')
with zipfile.ZipFile('native-probe-unsigned.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
 for p in d.rglob('*'):
  if p.is_file():z.write(p,p.relative_to(d.parent))
print('Built synthetic framework:',d)
