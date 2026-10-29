"""Reproducible, dependency-free application mark (ICO containing a PNG)."""

import struct
import zlib
from pathlib import Path

SIZE = 256
GLYPH = ("11110", "10001", "10001", "11110", "10100", "10010", "10001")


def pixel(x: int, y: int) -> tuple[int, int, int, int]:
    radius = 52
    cx, cy = max(radius, min(SIZE - radius, x)), max(radius, min(SIZE - radius, y))
    if (x - cx) ** 2 + (y - cy) ** 2 > radius**2:
        return (0, 0, 0, 0)
    column, row = (x - 73) // 22, (y - 51) // 22
    ink = 0 <= column < 5 and 0 <= row < 7 and GLYPH[row][column] == "1"
    return (241, 243, 246, 255) if ink else (36, 39, 44, 255)


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def main() -> None:
    rows = b"".join(b"\0" + bytes(c for x in range(SIZE) for c in pixel(x, y)) for y in range(SIZE))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )
    output = Path(__file__).resolve().parents[1] / "desktop/public/rancho.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(
        struct.pack("<HHH", 0, 1, 1)
        + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
        + png
    )


if __name__ == "__main__":
    main()
