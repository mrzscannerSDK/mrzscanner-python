"""Builds one platform wheel of the mrzscanner package from the package sources and a desktop
library build (platforms/desktop/build-lib.sh), with the standard library only.

    python make_wheel.py LIB_DIST PLATFORM_TAG OUT_DIR

LIB_DIST: platforms/desktop/dist/<target> (lib/ or bin/, tessdata/). PLATFORM_TAG: e.g.
manylinux_2_17_x86_64.manylinux2014_x86_64, win_amd64. The wheel is py3-none-PLATFORM_TAG: the
package is plain Python (ctypes), only the library is per platform.
"""

import base64
import hashlib
import re
import sys
import zipfile
from pathlib import Path

here = Path(__file__).resolve().parent
NAME = "mrzscanner"


def metadata(version: str) -> str:
    readme = (here / "README.md").read_text(encoding="utf-8")
    return "\n".join([
        "Metadata-Version: 2.1",
        f"Name: {NAME}",
        f"Version: {version}",
        "Summary: Reads the machine-readable zone (MRZ) of passports, ID cards and visas from images",
        "Home-page: https://mrzscanner.com/",
        "Author: Barkoder",
        "License: Proprietary",
        "Requires-Python: >=3.8",
        "Classifier: Programming Language :: Python :: 3",
        "Classifier: Operating System :: POSIX :: Linux",
        "Classifier: Operating System :: Microsoft :: Windows",
        "Classifier: License :: Other/Proprietary License",
        "Provides-Extra: images",
        'Requires-Dist: pillow; extra == "images"',
        "Description-Content-Type: text/markdown",
        "",
        readme,
    ])


def record_line(arc: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"{arc},sha256={digest},{len(data)}"


def main() -> None:
    dist, tag, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    src = here / "src" / NAME
    version = re.search(r'__version__ = "([^"]+)"', (src / "__init__.py").read_text()).group(1)
    files = {f"{NAME}/__init__.py": (src / "__init__.py").read_bytes()}
    libs = list((dist / "lib").glob("*.so")) + list((dist / "bin").glob("*.dll"))
    if not libs:
        sys.exit(f"no library in {dist}")
    for lib in libs:
        files[f"{NAME}/_lib/{lib.name}"] = lib.read_bytes()
    files[f"{NAME}/_lib/tessdata/mrz.traineddata"] = (dist / "tessdata" / "mrz.traineddata").read_bytes()
    info = f"{NAME}-{version}.dist-info"
    files[f"{info}/METADATA"] = metadata(version).encode()
    files[f"{info}/WHEEL"] = "\n".join(["Wheel-Version: 1.0", "Generator: mrzscanner make_wheel.py",
                                        "Root-Is-Purelib: false", f"Tag: py3-none-{tag}", ""]).encode()
    files[f"{info}/top_level.txt"] = f"{NAME}\n".encode()
    record = [record_line(a, d) for a, d in files.items()] + [f"{info}/RECORD,,"]
    files[f"{info}/RECORD"] = ("\n".join(record) + "\n").encode()
    out.mkdir(parents=True, exist_ok=True)
    wheel = out / f"{NAME}-{version}-py3-none-{tag}.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, data in files.items():
            zi = zipfile.ZipInfo(arc, date_time=(2020, 1, 1, 0, 0, 0))   # reproducible
            zi.external_attr = (0o755 if arc.endswith((".so", ".dll")) else 0o644) << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
    print(wheel)


if __name__ == "__main__":
    main()
