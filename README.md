# mrzscanner

Reads the machine-readable zone (MRZ) of passports, ID cards and visas from images, offline.
The MRZ scanner core in a self-contained library: no dependencies, no network.

```
pip install mrzscanner            # numpy arrays and raw pixels
pip install "mrzscanner[images]"  # also image files (Pillow)
```

Wheels: Linux x86_64 and arm64 (glibc 2.17 and up), Windows x64. Python 3.8 and up.

```python
import mrzscanner

mrzscanner.register_license("MRZ2...", app_id="com.example.app")   # without it some fields are masked

with mrzscanner.Scanner() as scanner:
    result = scanner.scan("passport.jpg")
    if result:
        print(result.text)                        # the MRZ lines, joined by newlines
        print(result.fields["document_number"])   # the parsed fields
        print(result.check_digits_valid, result.corners)
```

`scan` takes a file path or the bytes of an image file (with Pillow), a PIL image, a numpy array
(`uint8`, H x W gray, H x W x 3, H x W x 4), or raw pixels as `(bytes, width, height)`. Arrays
default to RGB / RGBA by channel count; OpenCV images are BGR: `scan(img, pixel_format="bgr")`.

Options: `document_types=mrzscanner.PASSPORT | mrzscanner.ID_CARD` (default all, `VISA` too),
`region=(x, y, width, height)` in percent of the image (only an MRZ inside it is read),
`upside_down=True` (also try the image turned 180 degrees).

A `Scanner` is used by one thread at a time; make one per thread. `register_license` is
process-wide: 0 when the key is accepted, a negative code otherwise (`-2` invalid, `-3` other
app, `-5` expired, `-6` other platform).

`result.fields` keys: `document_type_raw`, `document_type_readable`, `issuing_country`,
`surnames`, `given_names` (lists of words), `document_number`, `nationality`, `dob_raw` /
`dob_readable`, `sex`, `expiration_date_raw` / `expiration_date_readable`, and more as the
document has them.

Version 2.7.4. https://mrzscanner.com/
