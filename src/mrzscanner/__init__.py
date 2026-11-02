"""MRZ scanner: reads the machine-readable zone of passports, ID cards and visas from images.

The MRZ scanner core's C API (mrzscanner.h) through ctypes; the library and the legacy reader's
model ship in this package (_lib/). No dependencies; numpy arrays and Pillow images are accepted
when those are installed.

    import mrzscanner
    mrzscanner.register_license("MRZ2...", app_id="com.example.app")   # else fields are masked
    with mrzscanner.Scanner() as scanner:
        result = scanner.scan("passport.jpg")         # path, bytes, PIL image or numpy array
        if result:
            print(result.text, result.fields["document_number"])
        # the document's photo and signature too (a passport's data page; an ID card's MRZ side
        # is its back): result.portrait / result.signature, images like the one passed
        result = scanner.scan("passport.jpg", capture_portrait=True, capture_signature=True)

A Scanner is used by one thread at a time; make one per thread.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["Scanner", "Result", "Part", "LocatedDocument", "MrzError", "register_license", "version", "document_parts",
           "document_page", "locate_documents", "crop",
           "PASSPORT", "ID_CARD", "VISA", "ALL_DOCUMENTS", "PORTRAIT", "GHOST_PORTRAIT", "SIGNATURE", "MRZ", "CARD", "PAGE"]

__version__ = "2.7.4"   # platforms/sync-version.sh

# MRZ_DOC_* (mrzscanner.h)
PASSPORT, ID_CARD, VISA = 1, 2, 4
ALL_DOCUMENTS = PASSPORT | ID_CARD | VISA

# mrz_part_kind (mrzscanner.h)
PORTRAIT, GHOST_PORTRAIT, SIGNATURE, MRZ = 0, 1, 2, 3
# mrz_document_kind
CARD, PAGE = 0, 1

_OK, _NOT_FOUND = 0, 1
_FORMATS = {"rgba": 0, "bgra": 1, "rgb": 2, "bgr": 3, "gray": 4}
_CHANNELS = {"rgba": 4, "bgra": 4, "rgb": 3, "bgr": 3, "gray": 1}
_PLATFORM_DESKTOP = 4

_here = Path(__file__).resolve().parent / "_lib"


class _Image(ctypes.Structure):
    _fields_ = [("pixels", ctypes.c_void_p), ("width", ctypes.c_int32), ("height", ctypes.c_int32),
                ("stride", ctypes.c_int32), ("format", ctypes.c_int32)]


class _Options(ctypes.Structure):
    _fields_ = [("document_types", ctypes.c_int32), ("mode", ctypes.c_int32), ("effort", ctypes.c_int32),
                ("roi_x", ctypes.c_float), ("roi_y", ctypes.c_float),
                ("roi_width", ctypes.c_float), ("roi_height", ctypes.c_float),
                ("upside_down", ctypes.c_int32), ("validate_country_codes", ctypes.c_int32),
                ("ignore_invalid_check_digits", ctypes.c_int32), ("max_attempts", ctypes.c_int32),
                ("upscale_fallback", ctypes.c_int32), ("relaxed_ocr_fallback", ctypes.c_int32),
                ("relaxed_detection_fallback", ctypes.c_int32), ("candidate_finder", ctypes.c_int32),
                ("frame_confirmations", ctypes.c_int32), ("reader", ctypes.c_int32),
                ("rotation", ctypes.c_int32)]


class _Located(ctypes.Structure):
    _fields_ = [("kind", ctypes.c_int32), ("score", ctypes.c_float), ("corners", ctypes.c_float * 8)]


class _Part(ctypes.Structure):
    _fields_ = [("kind", ctypes.c_int32), ("score", ctypes.c_float), ("corners", ctypes.c_float * 8)]


def _load() -> ctypes.CDLL:
    path = os.environ.get("MRZSCANNER_LIBRARY")
    if not path:
        name = {"win32": "mrzscanner.dll", "darwin": "libmrzscanner.dylib"}.get(sys.platform, "libmrzscanner.so")
        path = str(_here / name)
    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(Path(path).parent))
    lib = ctypes.CDLL(path)
    p, i32, cp = ctypes.c_void_p, ctypes.c_int32, ctypes.c_char_p
    for name, res, args in [
        ("mrz_version", cp, []),
        ("mrz_status_string", cp, [i32]),
        ("mrz_register_license", i32, [cp, cp, cp, i32]),
        ("mrz_scan_options_init", None, [ctypes.POINTER(_Options)]),
        ("mrz_scanner_create", p, [cp, ctypes.POINTER(i32)]),
        ("mrz_scanner_destroy", None, [p]),
        ("mrz_scanner_set_blur_reading", None, [p, i32]),
        ("mrz_scan_image", i32, [p, ctypes.POINTER(_Image), ctypes.POINTER(_Options), ctypes.POINTER(p)]),
        ("mrz_result_json", cp, [p]),
        ("mrz_result_text", cp, [p]),
        ("mrz_result_check_digits_valid", i32, [p]),
        ("mrz_result_corners", i32, [p, ctypes.POINTER(i32)]),
        ("mrz_result_free", None, [p]),
        ("mrz_document_parts", i32, [ctypes.POINTER(_Image), ctypes.POINTER(i32), cp, ctypes.POINTER(_Part), i32]),
        ("mrz_document_page", i32, [ctypes.POINTER(_Image), ctypes.POINTER(i32), cp, ctypes.POINTER(ctypes.c_float)]),
        ("mrz_locate_documents", i32, [ctypes.POINTER(_Image), ctypes.POINTER(_Located), i32]),
        ("mrz_crop_size", i32, [ctypes.POINTER(ctypes.c_float), i32, ctypes.POINTER(i32)]),
        ("mrz_crop", i32, [ctypes.POINTER(_Image), ctypes.POINTER(ctypes.c_float), i32, i32, ctypes.c_void_p, i32]),
    ]:
        f = getattr(lib, name)
        f.restype, f.argtypes = res, args
    return lib


_lib = _load()


class MrzError(RuntimeError):
    """The scanner could not be created or an image could not be scanned."""


def version() -> str:
    """The core's version, e.g. "2.5.0"."""
    return _lib.mrz_version().decode()


def register_license(key: str, app_id: str, device_model: str = "") -> int:
    """Registers an MRZ2 licence key for this process (offline check). Returns 0 when accepted,
    -2 invalid key, -3 other app, -4 other device, -5 expired, -6 other platform. Without a
    licence, results come back with some fields masked."""
    return _lib.mrz_register_license(key.encode(), app_id.encode(), device_model.encode(), _PLATFORM_DESKTOP)


class Result:
    """One MRZ read: `text` (the lines joined by newlines), `fields` (the parsed fields, as the
    core returns them), `check_digits_valid`, and `corners` (4 (x, y) points in the image, in
    reading order) when known."""

    def __init__(self, text: str, fields: Dict[str, Any], check_digits_valid: bool,
                 corners: Optional[List[Tuple[int, int]]]):
        self.text = text
        self.fields = fields
        self.check_digits_valid = check_digits_valid
        self.corners = corners
        self.portrait: Any = None    # scan(capture_portrait=True): the document's photo, or None
        self.signature: Any = None   # scan(capture_signature=True): the holder's signature, or None
        self.document: Any = None    # scan(capture_document=True): the whole page around the MRZ, or None

    @property
    def lines(self) -> List[str]:
        return self.text.split("\n")

    def to_dict(self) -> Dict[str, Any]:
        return {"text": self.text, "fields": self.fields, "check_digits_valid": self.check_digits_valid,
                "corners": self.corners}

    def __repr__(self) -> str:
        return f"Result({self.text!r}, check_digits_valid={self.check_digits_valid})"


class Part:
    """A part of the document around a read MRZ: `kind` (PORTRAIT, GHOST_PORTRAIT, SIGNATURE, MRZ),
    `score` (0..1) and `corners` (4 (x, y) points in the image: top-left, top-right, bottom-right,
    bottom-left as the upright document shows it)."""

    def __init__(self, kind: int, score: float, corners: List[Tuple[float, float]]):
        self.kind, self.score, self.corners = kind, score, corners

    def __repr__(self) -> str:
        name = {PORTRAIT: "PORTRAIT", GHOST_PORTRAIT: "GHOST_PORTRAIT", SIGNATURE: "SIGNATURE", MRZ: "MRZ"}.get(self.kind)
        return f"Part({name}, score={self.score:.2f})"


def document_parts(image: Any, result: "Result", *, pixel_format: Optional[str] = None) -> List[Part]:
    """The parts of the document whose MRZ `result` was read in `image` (the same image): its photo,
    a ghost photo, the signature and the MRZ, best first within each kind. A small detector on the
    page placed around the MRZ, so only the MRZ's side: a passport's data page; an ID card's MRZ
    side is its back. Empty when the page cannot be placed or nothing is found."""
    if not result.corners:
        return []
    pixels, width, height, stride, fmt = _pixels(image, pixel_format)
    buf = (ctypes.c_uint8 * len(pixels)).from_buffer_copy(pixels) if isinstance(pixels, bytes) else pixels
    img = _Image(ctypes.cast(buf, ctypes.c_void_p), width, height, stride, _FORMATS[fmt])
    xy = (ctypes.c_int32 * 8)(*[int(v) for p in result.corners for v in p])
    out = (_Part * 16)()
    n = _lib.mrz_document_parts(ctypes.byref(img), xy, result.text.encode(), out, 16)
    if n < 0:
        raise MrzError(_lib.mrz_status_string(n).decode())
    return [Part(out[i].kind, out[i].score, [(out[i].corners[2 * k], out[i].corners[2 * k + 1]) for k in range(4)])
            for i in range(min(n, 16))]


def document_page(image: Any, result: "Result", *, pixel_format: Optional[str] = None) -> Optional[List[Tuple[float, float]]]:
    """The page around the MRZ `result` was read from in `image` (the same image): its 4 corners
    (top-left, top-right, bottom-right, bottom-left as the upright page shows it), placed from the
    MRZ and fitted to the page's edges; None when it cannot be placed. Cut it out with crop()."""
    if not result.corners:
        return None
    pixels, width, height, stride, fmt = _pixels(image, pixel_format)
    buf = (ctypes.c_uint8 * len(pixels)).from_buffer_copy(pixels) if isinstance(pixels, bytes) else pixels
    img = _Image(ctypes.cast(buf, ctypes.c_void_p), width, height, stride, _FORMATS[fmt])
    xy = (ctypes.c_int32 * 8)(*[int(v) for p in result.corners for v in p])
    page = (ctypes.c_float * 8)()
    n = _lib.mrz_document_page(ctypes.byref(img), xy, result.text.encode(), page)
    if n < 0:
        raise MrzError(_lib.mrz_status_string(n).decode())
    return [(page[2 * k], page[2 * k + 1]) for k in range(4)] if n == 1 else None


class LocatedDocument:
    """A document found without an MRZ: `kind` (CARD: an ID-1 card; PAGE: a passport / visa page),
    `score` (0..1) and `corners` (4 (x, y) points, clockwise from the one nearest the image's
    top-left - which way the document is up is not known)."""

    def __init__(self, kind: int, score: float, corners: List[Tuple[float, float]]):
        self.kind, self.score, self.corners = kind, score, corners

    def __repr__(self) -> str:
        return f"LocatedDocument({'PAGE' if self.kind == PAGE else 'CARD'}, score={self.score:.2f})"


def locate_documents(image: Any, *, pixel_format: Optional[str] = None) -> List[LocatedDocument]:
    """The documents in `image` found without reading an MRZ (an ID card's front, a page whose MRZ
    is not in view), best first. Cut one out with crop(image, doc.corners)."""
    pixels, width, height, stride, fmt = _pixels(image, pixel_format)
    buf = (ctypes.c_uint8 * len(pixels)).from_buffer_copy(pixels) if isinstance(pixels, bytes) else pixels
    img = _Image(ctypes.cast(buf, ctypes.c_void_p), width, height, stride, _FORMATS[fmt])
    out = (_Located * 8)()
    n = _lib.mrz_locate_documents(ctypes.byref(img), out, 8)
    if n < 0:
        raise MrzError(_lib.mrz_status_string(n).decode())
    return [LocatedDocument(out[i].kind, out[i].score, [(out[i].corners[2 * k], out[i].corners[2 * k + 1]) for k in range(4)])
            for i in range(min(n, 8))]


def crop(image: Any, corners: Sequence[Tuple[float, float]], height: Optional[int] = None, *,
         pixel_format: Optional[str] = None) -> Any:
    """The area inside `corners` (a Part's) cut out of `image`, straightened, `height` pixels tall
    (default: its own height in the image). Returned like the image given: a numpy array for an
    array, a PIL image for a PIL image or an image file, (bytes, width, height) for raw pixels."""
    pixels, width, height_in, stride, fmt = _pixels(image, pixel_format)
    xy = (ctypes.c_float * 8)(*[float(v) for p in corners for v in p])
    if height is None:
        import math
        left = math.dist(corners[0], corners[3])
        right = math.dist(corners[1], corners[2])
        height = max(8, round((left + right) / 2))
    w = ctypes.c_int32()
    if _lib.mrz_crop_size(xy, int(height), ctypes.byref(w)) != _OK:
        raise MrzError("these corners enclose no area")
    ch = _CHANNELS[fmt]
    out = (ctypes.c_uint8 * (w.value * int(height) * ch))()
    buf = (ctypes.c_uint8 * len(pixels)).from_buffer_copy(pixels) if isinstance(pixels, bytes) else pixels
    img = _Image(ctypes.cast(buf, ctypes.c_void_p), width, height_in, stride, _FORMATS[fmt])
    status = _lib.mrz_crop(ctypes.byref(img), xy, w.value, int(height), out, 0)
    if status != _OK:
        raise MrzError(_lib.mrz_status_string(status).decode())
    data = bytes(out)
    if hasattr(image, "__array_interface__"):
        import numpy as np
        a = np.frombuffer(data, np.uint8).reshape(int(height), w.value, ch)
        return a[:, :, 0].copy() if ch == 1 and np.asarray(image).ndim == 2 else a.copy()
    if isinstance(image, tuple):
        return data, w.value, int(height)
    from PIL import Image
    mode = {"rgb": "RGB", "rgba": "RGBA", "gray": "L"}[fmt]
    return Image.frombytes(mode, (w.value, int(height)), data)


class Scanner:
    """Scans still images. Use one per thread; `close()` (or `with`) frees it."""

    def __init__(self, tessdata_dir: Optional[str] = None):
        # Tesseract 3 wants the folder named tessdata, with the trailing slash.
        tessdata = str(Path(tessdata_dir) if tessdata_dir else _here / "tessdata")
        if not tessdata.endswith(("/", "\\")):
            tessdata += os.sep
        status = ctypes.c_int32(0)
        self._s = _lib.mrz_scanner_create(tessdata.encode(), ctypes.byref(status))
        if not self._s:
            raise MrzError(f"MRZ scanner could not be created: {_lib.mrz_status_string(status.value).decode()}")

    def close(self) -> None:
        if self._s:
            _lib.mrz_scanner_destroy(self._s)
            self._s = None

    def __enter__(self) -> "Scanner":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def scan(self, image: Any, *, pixel_format: Optional[str] = None, document_types: int = ALL_DOCUMENTS,
             region: Optional[Sequence[float]] = None, upside_down: bool = False,
             capture_portrait: bool = False, capture_signature: bool = False, capture_document: bool = False,
             blur_reading: bool = False, enforce_check_digits: bool = True) -> Optional[Result]:
        """The MRZ in `image`, or None when there is none.

        image: a file path, the bytes of an image file (both need Pillow), a PIL image, a numpy
          array (H x W gray, H x W x 3, H x W x 4; uint8), or (bytes, width, height) raw pixels.
        pixel_format: "rgb", "bgr", "rgba", "bgra" or "gray" for arrays and raw pixels; default by
          channel count: gray, rgb, rgba (OpenCV arrays are BGR: pass "bgr").
        document_types: PASSPORT | ID_CARD | VISA (default all).
        region: (x, y, width, height) in percent of the image: only an MRZ inside it is read.
        capture_portrait / capture_signature: also result.portrait / result.signature (see
          document_parts and crop), images like `image`; None when not found.
        capture_document: also result.document, the whole page around the MRZ, an image like `image`
          (None when the page cannot be placed).
        blur_reading: anti-blur - a second character model, trained on defocus-blurred MRZs, reads
          what the first cannot (a photo out of focus); only when the normal read fails. Names have
          no check digit: on a blurred MRZ a letter is occasionally wrong.
        enforce_check_digits: True (default) returns only reads whose check digits all match; False
          also returns the best read whose check digits do not (result.check_digits_valid False) -
          specimens and sample documents often print digits that do not add up.
        """
        if not self._s:
            raise MrzError("scanner is closed")
        pixels, width, height, stride, fmt = _pixels(image, pixel_format)
        buf = (ctypes.c_uint8 * len(pixels)).from_buffer_copy(pixels) if isinstance(pixels, bytes) else pixels
        img = _Image(ctypes.cast(buf, ctypes.c_void_p), width, height, stride, _FORMATS[fmt])
        _lib.mrz_scanner_set_blur_reading(self._s, int(blur_reading))
        opts = _Options()
        _lib.mrz_scan_options_init(ctypes.byref(opts))
        opts.document_types = document_types
        opts.upside_down = int(upside_down)
        opts.ignore_invalid_check_digits = int(enforce_check_digits)
        if region is not None:
            opts.roi_x, opts.roi_y, opts.roi_width, opts.roi_height = (float(v) for v in region)
        res = ctypes.c_void_p()
        status = _lib.mrz_scan_image(self._s, ctypes.byref(img), ctypes.byref(opts), ctypes.byref(res))
        try:
            if status == _NOT_FOUND or not res:
                return None
            if status != _OK:
                raise MrzError(_lib.mrz_status_string(status).decode())
            xy = (ctypes.c_int32 * 8)()
            corners = ([(xy[2 * k], xy[2 * k + 1]) for k in range(4)]
                       if _lib.mrz_result_corners(res, xy) == 1 else None)
            result = Result(_lib.mrz_result_text(res).decode(), json.loads(_lib.mrz_result_json(res).decode()),
                            _lib.mrz_result_check_digits_valid(res) == 1, corners)
        finally:
            _lib.mrz_result_free(res)
        if capture_portrait or capture_signature:
            parts = document_parts(image, result, pixel_format=pixel_format)
            best = {}
            for p in parts:
                best.setdefault(p.kind, p)
            if capture_portrait and PORTRAIT in best:
                result.portrait = crop(image, best[PORTRAIT].corners, pixel_format=pixel_format)
            if capture_signature and SIGNATURE in best:
                result.signature = crop(image, best[SIGNATURE].corners, pixel_format=pixel_format)
        if capture_document:
            page = document_page(image, result, pixel_format=pixel_format)
            if page:
                result.document = crop(image, page, pixel_format=pixel_format)
        return result


def _pixels(image: Any, pixel_format: Optional[str]):
    """(pixels: bytes or a ctypes buffer, width, height, stride, format) for the C API."""
    if isinstance(image, (str, os.PathLike, bytes, bytearray)) and not isinstance(image, tuple):
        try:
            from PIL import Image
        except ImportError as e:
            raise MrzError("reading image files needs Pillow (pip install pillow); "
                           "or pass a numpy array or raw pixels") from e
        import io
        src = io.BytesIO(bytes(image)) if isinstance(image, (bytes, bytearray)) else image
        with Image.open(src) as im:
            from PIL import ImageOps
            return _pixels(ImageOps.exif_transpose(im).convert("RGB"), None)
    if isinstance(image, tuple):
        data, width, height = image[:3]
        fmt = pixel_format or "rgb"
        return bytes(data), int(width), int(height), 0, fmt
    if hasattr(image, "mode") and hasattr(image, "tobytes"):   # a PIL image
        im = image if image.mode in ("RGB", "RGBA", "L") else image.convert("RGB")
        fmt = {"RGB": "rgb", "RGBA": "rgba", "L": "gray"}[im.mode]
        return im.tobytes(), im.width, im.height, 0, fmt
    if hasattr(image, "__array_interface__"):                  # a numpy array
        import numpy as np
        a = np.ascontiguousarray(image)
        if a.dtype != np.uint8:
            raise MrzError(f"pixel arrays must be uint8, not {a.dtype}")
        if a.ndim == 3 and a.shape[2] == 1:
            a = a[:, :, 0].copy()
        channels = 1 if a.ndim == 2 else a.shape[2]
        fmt = pixel_format or {1: "gray", 3: "rgb", 4: "rgba"}.get(channels)
        if fmt is None or _CHANNELS[fmt] != channels:
            raise MrzError(f"{channels} channels do not match pixel format {fmt!r}")
        return a.tobytes(), a.shape[1], a.shape[0], 0, fmt
    raise MrzError(f"unsupported image type {type(image).__name__}")
