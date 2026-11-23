"""Build the official v1.5.0 iPad shell with the existing four game frameworks."""

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

import nx2apple_build as build

TAG = "nx2apple-shared-shell-v1.5.0-20261123"
SOURCE_TAG = "nx2apple-bananza-library-v458752"
TITLE_IDS = {"0100000000010000", "010036B0034E4000", "01007EF00011E000", "040074A01BF12000"}
RELEASE_COMMIT = "f82be32dd41cbb167b66d8a85cedab947415f81b"
ENGINE_PIN = "99224562186a8fdb7e97afb872319a21941ec317"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_frameworks(ipa, output):
    frameworks = []
    with zipfile.ZipFile(ipa) as archive:
        for title_id in sorted(TITLE_IDS):
            name = f"DmGame{title_id}.framework"
            prefix = f"Payload/DmShell.app/Frameworks/{name}/"
            framework = output / name
            for entry in archive.infolist():
                if not entry.filename.startswith(prefix) or entry.is_dir():
                    continue
                relative = Path(entry.filename.removeprefix(prefix))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"Invalid framework path: {relative}")
                destination = framework / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                mode = (entry.external_attr >> 16) & 0o777
                if mode:
                    destination.chmod(mode)
            manifest = json.loads((framework / "game.manifest.json").read_text())
            if manifest["title_id"] != title_id or manifest["loader"] != "FH":
                raise ValueError(f"Unexpected manifest in {name}")
            (framework / manifest["framework_executable"]).chmod(0o755)
            frameworks.append(framework)
    return frameworks


def main():
    assert build.capture(["gh", "api", "repos/" + build.REPO, "--jq", ".private"]).strip() == "true"
    assert build.capture(["git", "-C", build.NX, "rev-parse", "HEAD"]).strip() == RELEASE_COMMIT
    assert (build.NX / ".upstream-ref").read_text().strip() == ENGINE_PIN
    build.DATA.mkdir(parents=True, exist_ok=True)
    build.release(TAG)
    try:
        base = build.DATA / "base"
        build.restore_files(SOURCE_TAG, "output-manifest.json", base, {"NX2Apple.ipa"})
        frameworks = extract_frameworks(base / "NX2Apple.ipa", build.DATA / "frameworks")
        shell = build.DATA / "shell"
        env = dict(
            os.environ,
            SHELL_DIR=str(shell),
            DM_ENGINE_DIR=str(build.ENGINE),
            DM_SHELL_FRAMEWORKS="\n".join(map(str, frameworks)),
            DM_BUNDLE_ID="org.nxnative.shell",
            DM_APP_VERSION="1.5.0",
        )
        build.run(["bash", build.NX / "frontend-ios/Shell/build_shell.sh", "--build"], env=env)
        app = shell / "dd/Build/Products/Release-iphoneos/DmShell.app"
        # Preserve the memory entitlement requested by the stock project for local signing.
        build.run(["xattr", "-cr", app])
        build.run(["codesign", "--force", "--sign", "-", "--entitlements",
                   shell / "Gen/DmShell.entitlements", app / "DmShell"])
        hashes = {}
        for framework in frameworks:
            manifest = json.loads((framework / "game.manifest.json").read_text())
            executable = manifest["framework_executable"]
            actual = digest(app / "Frameworks" / framework.name / executable)
            if actual != digest(framework / executable):
                raise ValueError(f"Embedded executable changed: {framework.name}")
            hashes[manifest["title_id"]] = actual
        ipa = build.DATA / "NX2Apple-v1.5.0-shell-unsigned.ipa"
        with zipfile.ZipFile(ipa, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for file in sorted(app.rglob("*")):
                if file.is_file():
                    archive.write(file, Path("Payload/DmShell.app") / file.relative_to(app))
        with zipfile.ZipFile(ipa) as archive:
            assert archive.testzip() is None
        manifest = {
            "schema": 1,
            "version": "1.5.0",
            "nx2apple_commit": RELEASE_COMMIT,
            "engine_pin": ENGINE_PIN,
            "embedded_legacy_executables": hashes,
            "files": [build.upload_file(TAG, ipa)],
        }
        result = build.DATA / "output-manifest.json"
        result.write_text(json.dumps(manifest, indent=2) + "\n")
        build.run(["gh", "release", "upload", TAG, result, "--repo", build.REPO, "--clobber"])
    finally:
        build.diagnostics(TAG)


if __name__ == "__main__":
    main()
