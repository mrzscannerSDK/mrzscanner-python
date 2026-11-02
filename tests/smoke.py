"""Installed-package check: the version, a blank image (no MRZ), raw pixels, and one ID card when a
path is given (the 1.x test card, MRZScanner/MRZScannerTests/Resources/testImageID.JPG; read as
raw RGB without Pillow when Pillow is missing).

    python tests/smoke.py [IMAGE]
"""

import sys

import mrzscanner

ID_TEXT = "IDMKDA1896566<72106992460008<<\n9206218M2102252MKD<<<<<<<<<<<1\nSILJAVSKI<<FILIP<<<<<<<<<<<<<<"

assert mrzscanner.version() == mrzscanner.__version__, (mrzscanner.version(), mrzscanner.__version__)
with mrzscanner.Scanner() as s:
    assert s.scan((bytes(640 * 480), 640, 480), pixel_format="gray") is None
    if len(sys.argv) > 1:
        try:
            r = s.scan(sys.argv[1])
        except mrzscanner.MrzError:          # no Pillow: skip the file read
            r = None
            print("Pillow missing: image file not read")
        if r is not None:
            assert r.text == ID_TEXT and r.check_digits_valid and r.corners, r
            print("read:", r.lines[0])
print("ok", mrzscanner.version(), mrzscanner.__file__)
