"""Build a history-free source tarball from a clean, checked HEAD."""
import gzip
import hashlib
import io
from pathlib import Path
import re
import tarfile
import check_public as public


def main():
    if public.git("status", "--porcelain").strip():
        raise SystemExit("Commit public changes and clean the working tree first.")
    findings = public.check_tree("HEAD")
    if findings:
        raise SystemExit("Public-file check failed: " + repr(findings))
    version = public.git("show", "HEAD:VERSION").decode().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?", version):
        raise SystemExit("Invalid VERSION")
    prefix = "echo-" + version
    destination = public.ROOT / "dist"
    destination.mkdir(exist_ok=True)
    output = destination / (prefix + ".tar.gz")
    # Build ordinary tar entries: no Git archive metadata, commit IDs or authors.
    with output.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as archive:
                for entry in public.git("ls-tree", "-rz", "HEAD").decode().split("\0"):
                    if not entry:
                        continue
                    meta, path = entry.split("\t", 1)
                    mode, kind, _ = meta.split()
                    if kind != "blob" or mode not in {"100644", "100755"}:
                        raise SystemExit("Unsupported release entry: " + path)
                    data = public.git("show", "HEAD:" + path)
                    info = tarfile.TarInfo(prefix + "/" + path)
                    info.size = len(data)
                    info.mode = 0o755 if mode == "100755" else 0o644
                    info.mtime = 0
                    archive.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(digest + "  " + output.name + "\n")
    print(output)
    print("SHA256:", digest)


if __name__ == "__main__":
    main()
