"""
SHP(TD) file reader for C&C / OpenRA SHP format.

Supports two variants:
  - OpenRA SHP(TD): 12-byte file header (num_frames, xpos, ypos, width, height, delta_size),
    followed by (num_frames+1) x 8-byte frame entries (uint24 offset + byte flags + uint24 ref + byte ref_flags),
    then LCW-compressed frame data.
  - Legacy (written by shp_writer.py): 6-byte header (num_frames, file_size_lo, file_size_hi),
    followed by (num_frames+1) x 8-byte entries (uint32 offset + uint16 ctype + uint16 ref),
    then raw uncompressed frame data blocks with a 4-byte block header.
"""

import struct


# ---------------------------------------------------------------------------
# LCW / Format80 decompressor
# ---------------------------------------------------------------------------

def _decompress_lcw(data: bytes, expected_size: int = 0) -> bytes:
    """
    Decompress Westwood LCW (Format 80) compressed data.
    Used for compression flag 0x80 in SHP(TD) frames.
    """
    out = bytearray()
    i = 0
    n = len(data)

    while i < n:
        cmd = data[i]

        if cmd & 0x80 == 0:
            # 0xxxxxxx  xxxxxxxx  — short relative copy
            # count = upper nibble of cmd (bits 4-6) + 3
            # back  = lower nibble of cmd (bits 0-3) << 8 | next_byte
            if i + 1 >= n:
                break
            count = ((cmd >> 4) & 0x07) + 3
            back  = ((cmd & 0x0F) << 8) | data[i + 1]
            i += 2
            pos = len(out) - back
            for j in range(count):
                out.append(out[pos + j] if 0 <= pos + j < len(out) else 0)

        elif cmd & 0xC0 == 0x80:
            # 10xxxxxx  — literal run; count = lower 6 bits
            count = cmd & 0x3F
            if count == 0:
                # 0x80 with count 0 = end of stream
                break
            out.extend(data[i + 1: i + 1 + count])
            i += 1 + count

        else:
            # 11xxxxxx  — extended commands
            if cmd == 0xFE:
                # Repeat single byte: FE cc cc vv
                if i + 3 >= n:
                    break
                count = struct.unpack_from('<H', data, i + 1)[0]
                value = data[i + 3]
                out.extend(bytes([value]) * count)
                i += 4

            elif cmd == 0xFF:
                # Long absolute copy: FF cc cc pp pp
                if i + 4 >= n:
                    break
                count  = struct.unpack_from('<H', data, i + 1)[0]
                offset = struct.unpack_from('<H', data, i + 3)[0]
                for j in range(count):
                    out.append(out[offset + j] if 0 <= offset + j < len(out) else 0)
                i += 5

            else:
                # 0xC0–0xFD: medium absolute copy
                # count = lower 6 bits + 3; next 2 bytes = absolute dest offset
                if i + 2 >= n:
                    break
                count  = (cmd & 0x3F) + 3
                offset = struct.unpack_from('<H', data, i + 1)[0]
                for j in range(count):
                    out.append(out[offset + j] if 0 <= offset + j < len(out) else 0)
                i += 3

    return bytes(out)


# ---------------------------------------------------------------------------
# Format detection helpers
# ---------------------------------------------------------------------------

def _is_legacy_format(data: bytes) -> bool:
    """
    Returns True if the file was written by shp_writer.py (legacy format):
      bytes 2-5 encode the 32-bit file size (lo word + hi word).
    """
    if len(data) < 6:
        return False
    file_size   = len(data)
    encoded_lo  = struct.unpack_from('<H', data, 2)[0]
    encoded_hi  = struct.unpack_from('<H', data, 4)[0]
    encoded_sz  = encoded_lo | (encoded_hi << 16)
    return encoded_sz == file_size


def _is_alternate_format(data: bytes) -> bool:
    """
    Returns True for the alternate SHP format where bytes[0:2] == 0x0000.
    This format stores num_frames at bytes[2:4] and uses 24-byte per-frame
    entries starting at byte 12, each with individual x/y/width/height.
    Used by barr.shp, crane-tur.shp, etc.
    """
    if len(data) < 12:
        return False
    return struct.unpack_from('<H', data, 0)[0] == 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SHPFrame:
    """One decoded frame from an SHP file."""
    __slots__ = ('pixels', 'width', 'height', 'x', 'y', 'compression')

    def __init__(self, pixels, width, height, x=0, y=0, compression=0):
        self.pixels      = pixels      # list[int] of palette indices, len = width*height
        self.width       = width
        self.height      = height
        self.x           = x           # virtual canvas x offset
        self.y           = y           # virtual canvas y offset
        self.compression = compression # raw compression byte from entry


class SHPFile:
    """Loaded SHP file with all frames decoded."""

    def __init__(self):
        self.frames      = []   # list[SHPFrame]
        self.width       = 0
        self.height      = 0
        self.num_frames  = 0
        self.raw_data    = None  # original bytes for safe re-save
        self._format     = 'openra'  # 'openra' | 'legacy'


def read_shp(filename: str) -> SHPFile:
    """
    Read an SHP file and return an SHPFile object with all frames decoded.
    Raises ValueError for unrecognised or corrupt files.
    """
    with open(filename, 'rb') as f:
        data = f.read()

    if len(data) < 6:
        raise ValueError("File too small to be an SHP file.")

    shp = SHPFile()
    shp.raw_data = data

    if _is_alternate_format(data):
        _read_alternate(data, shp)
    elif _is_legacy_format(data):
        _read_legacy(data, shp)
    else:
        _read_openra(data, shp)

    return shp


# ---------------------------------------------------------------------------
# Line-by-line RLE decompressor  (used by alternate SHP format)
# ---------------------------------------------------------------------------

def _decompress_line_rle(data: bytes, width: int, height: int) -> bytes:
    """
    Line-by-line RLE format used in alternate SHP files (compression type 3).

    Each row:
      uint16  row_total_bytes  (including this 2-byte header)
      RLE data:
        0x00 N  →  N transparent (palette index 0) pixels
        V       →  single opaque pixel with value V  (V != 0)
    """
    out = bytearray()
    pos = 0
    n   = len(data)

    for _ in range(height):
        if pos + 2 > n:
            out.extend(b'\x00' * width)
            continue

        row_len = struct.unpack_from('<H', data, pos)[0]
        row_end = pos + row_len
        pos    += 2

        row_pixels = []
        while pos < row_end and pos < n:
            byte = data[pos]; pos += 1
            if byte == 0:
                if pos < row_end and pos < n:
                    count = data[pos]; pos += 1
                    row_pixels.extend(b'\x00' * count)
            else:
                row_pixels.append(byte)

        pos = row_end  # skip any leftover / align to next row

        # Clip or pad to exact width
        row_pixels = row_pixels[:width]
        while len(row_pixels) < width:
            row_pixels.append(0)
        out.extend(row_pixels)

    return bytes(out)


# ---------------------------------------------------------------------------
# Alternate SHP format reader  (bytes[0:2] == 0, e.g. barr.shp)
# ---------------------------------------------------------------------------

def _read_alternate(data: bytes, shp: SHPFile) -> None:
    """
    Parse the alternate SHP format used by barr.shp, crane-tur.shp, etc.

    File header (8 bytes):
      uint16  zero          (0x0000 — format marker)
      uint16  canvas_width
      uint16  canvas_height
      uint16  num_entries   (count of index entries that follow)

    Per-frame entries (24 bytes each), starting at byte 8:
      uint16  x             placement offset within canvas
      uint16  y             placement offset within canvas
      uint16  width         pixel data width
      uint16  height        pixel data height
      uint16  compression   (0=raw, 2=LCW, 3=LCW+XOR with previous frame)
      uint16  flags2
      uint32  hash          (ignored)
      uint32  zero
      uint32  file_offset   (byte offset of compressed data in file)

    Frame data at file_offset, compressed per compression field.
    XOR (type 3) applies to the raw decompressed pixel data (w×h),
    not the full canvas — tracked per (w,h) bucket.
    """
    if len(data) < 8:
        raise ValueError("Alternate SHP header too short.")

    canvas_w    = struct.unpack_from('<H', data, 2)[0]
    canvas_h    = struct.unpack_from('<H', data, 4)[0]
    num_entries = struct.unpack_from('<H', data, 6)[0]

    if num_entries == 0:
        raise ValueError("Alternate SHP: num_entries is 0.")

    shp.num_frames = num_entries
    shp.width      = canvas_w
    shp.height     = canvas_h
    shp._format    = 'alternate'

    ENTRY_SIZE  = 24
    INDEX_START = 8

    frames_info = []
    for i in range(num_entries):
        entry_off = INDEX_START + i * ENTRY_SIZE
        if entry_off + ENTRY_SIZE > len(data):
            break
        x           = struct.unpack_from('<H', data, entry_off +  0)[0]
        y           = struct.unpack_from('<H', data, entry_off +  2)[0]
        width       = struct.unpack_from('<H', data, entry_off +  4)[0]
        height      = struct.unpack_from('<H', data, entry_off +  6)[0]
        compression = struct.unpack_from('<H', data, entry_off +  8)[0]
        file_offset = struct.unpack_from('<I', data, entry_off + 20)[0]
        frames_info.append((x, y, width, height, compression, file_offset))

    for i, (x, y, w, h, compression, offset) in enumerate(frames_info):
        expected   = w * h if (w > 0 and h > 0) else 0
        canvas_pix = [0] * (canvas_w * canvas_h)

        if expected > 0:
            # Find end of this frame's data (next frame with a higher offset)
            next_offset = len(data)
            for j in range(i + 1, len(frames_info)):
                c = frames_info[j][5]
                if c > offset:
                    next_offset = c
                    break

            if offset < len(data):
                frame_bytes = data[offset:next_offset]
                if compression == 0:
                    raw = list(frame_bytes[:expected])
                    while len(raw) < expected:
                        raw.append(0)
                elif compression == 2:
                    raw = list(_decompress_lcw(frame_bytes, expected)[:expected])
                    while len(raw) < expected:
                        raw.append(0)
                else:
                    # compression == 3 (and unknown values): line-by-line RLE
                    raw = list(_decompress_line_rle(frame_bytes, w, h))

                # Embed raw (w×h) at (x, y) on canvas
                for fy in range(h):
                    for fx in range(w):
                        dst = (y + fy) * canvas_w + (x + fx)
                        src = fy * w + fx
                        if 0 <= dst < len(canvas_pix):
                            canvas_pix[dst] = raw[src]

        shp.frames.append(SHPFrame(canvas_pix, canvas_w, canvas_h, x, y, compression))


# ---------------------------------------------------------------------------
# OpenRA SHP(TD) reader
# ---------------------------------------------------------------------------

def _read_openra(data: bytes, shp: SHPFile) -> None:
    """
    Parse the standard OpenRA SHP(TD) format.

    File header (12 bytes):
      uint16 num_frames
      uint16 x_pos   (ignored)
      uint16 y_pos   (ignored)
      uint16 width
      uint16 height
      uint16 delta_size (ignored)

    Frame index entries (8 bytes each, num_frames+1 total):
      uint24 data_offset  (3 bytes LE)
      uint8  flags        (0x80=LCW, 0x00=raw)
      uint24 ref_offset   (3 bytes LE, for XOR modes)
      uint8  ref_flags

    Sentinel entry: data_offset = file_size.
    """
    if len(data) < 12:
        raise ValueError("Header too short for OpenRA SHP format.")

    num_frames = struct.unpack_from('<H', data, 0)[0]
    width      = struct.unpack_from('<H', data, 6)[0]
    height     = struct.unpack_from('<H', data, 8)[0]

    shp.num_frames = num_frames
    shp.width      = width
    shp.height     = height
    shp._format    = 'openra'

    expected_pixels = width * height
    index_start     = 14   # 6-byte file header + 8-byte dimensions entry
    entry_size      = 8

    for i in range(num_frames):
        entry_off = index_start + i * entry_size
        if entry_off + 8 > len(data):
            raise ValueError(f"Frame index entry {i} out of bounds.")

        # uint24 LE offset: 3 bytes
        b0, b1, b2 = data[entry_off], data[entry_off + 1], data[entry_off + 2]
        offset      = b0 | (b1 << 8) | (b2 << 16)
        flags       = data[entry_off + 3]

        if offset >= len(data):
            raise ValueError(f"Frame {i} offset {offset} exceeds file size {len(data)}.")

        # Determine next frame's offset to know how many compressed bytes to read
        next_entry_off = index_start + (i + 1) * entry_size
        if next_entry_off + 3 <= len(data):
            nb0, nb1, nb2 = data[next_entry_off], data[next_entry_off+1], data[next_entry_off+2]
            next_offset = nb0 | (nb1 << 8) | (nb2 << 16)
        else:
            next_offset = len(data)

        frame_bytes = data[offset:next_offset]

        if flags == 0x80:
            # LCW compressed
            pixels_raw = _decompress_lcw(frame_bytes, expected_pixels)
        else:
            # Uncompressed raw bytes
            pixels_raw = frame_bytes

        # Pad or clip to exact frame size
        pixels = list(pixels_raw[:expected_pixels])
        while len(pixels) < expected_pixels:
            pixels.append(0)

        shp.frames.append(SHPFrame(pixels, width, height, compression=flags))


# ---------------------------------------------------------------------------
# Legacy format reader  (files written by shp_writer.py)
# ---------------------------------------------------------------------------

def _read_legacy(data: bytes, shp: SHPFile) -> None:
    """
    Parse the legacy shp_writer.py format.

    File header (6 bytes):
      uint16 num_frames
      uint16 file_size_lo
      uint16 file_size_hi

    Frame entries (8 bytes each, num_frames+1 total):
      uint32 offset
      uint16 compression  (0 = raw)
      uint16 ref_frame

    Frame data blocks at each offset:
      uint16 compressed_size
      uint16 uncompressed_size
      raw pixel data
    """
    num_frames = struct.unpack_from('<H', data, 0)[0]
    shp.num_frames = num_frames
    shp._format    = 'legacy'

    # Read all offsets first; deduce frame size from uncompressed_size
    index_start = 6
    offsets = []
    for i in range(num_frames):
        entry_off  = index_start + i * 8
        offset     = struct.unpack_from('<I', data, entry_off)[0]
        offsets.append(offset)

    width = height = 0
    frames_pixels = []

    for i, offset in enumerate(offsets):
        if offset + 4 > len(data):
            break
        compressed_sz   = struct.unpack_from('<H', data, offset)[0]
        uncompressed_sz = struct.unpack_from('<H', data, offset + 2)[0]
        raw = data[offset + 4: offset + 4 + uncompressed_sz]

        pixels = list(raw)
        if i == 0 and uncompressed_sz > 0:
            # Infer square dimensions if not yet known
            import math
            side = int(math.isqrt(uncompressed_sz))
            if side * side == uncompressed_sz:
                width = height = side
            else:
                # Try to find a reasonable aspect ratio
                for w in range(1, uncompressed_sz + 1):
                    if uncompressed_sz % w == 0:
                        h = uncompressed_sz // w
                        if abs(w - h) <= max(w, h) // 2:
                            width, height = w, h
                            break
                if width == 0:
                    width = uncompressed_sz
                    height = 1

        frames_pixels.append(pixels)

    shp.width  = width
    shp.height = height

    expected = width * height
    for pixels in frames_pixels:
        px = list(pixels[:expected])
        while len(px) < expected:
            px.append(0)
        shp.frames.append(SHPFrame(px, width, height))


# ---------------------------------------------------------------------------
# Save (round-trip write preserving format)
# ---------------------------------------------------------------------------

def write_shp_openra(filename: str, shp: SHPFile) -> None:
    """
    Save an SHPFile back to disk in OpenRA SHP(TD) format.
    Uses LCW compression (simple passthrough as raw bytes wrapped in LCW literal runs).
    """
    import os

    frames  = shp.frames
    n       = len(frames)
    width   = shp.width
    height  = shp.height

    # Compress each frame as LCW literal chunks (simplest valid LCW = literal runs)
    def _lcw_encode_raw(pixels: list) -> bytes:
        """
        Encode pixel data as LCW using only literal-run commands (0x80-0xBF).
        Safe and simple — no size reduction but always valid.
        """
        raw   = bytes(pixels)
        out   = bytearray()
        i     = 0
        while i < len(raw):
            chunk = raw[i:i + 63]   # max 63 per literal run (0x80 | 63 = 0xBF)
            out.append(0x80 | len(chunk))
            out.extend(chunk)
            i += 63
        out.append(0x80)  # end-of-stream (count=0 literal)
        return bytes(out)

    compressed_frames = []
    for frame in frames:
        px = list(frame.pixels[:width * height])
        while len(px) < width * height:
            px.append(0)
        compressed_frames.append(_lcw_encode_raw(px))

    # Build file header + index
    # OpenRA SHP(TD) requires n + 2 index entries: n frame entries + 1 sentinel + 1 trailing zero entry
    INDEX_START = 14       # 6-byte header + 8-byte dimensions entry
    ENTRY_SIZE  = 8
    num_entries = n + 2   # +1 sentinel +1 trailing zero entry

    data_start  = INDEX_START + num_entries * ENTRY_SIZE
    offsets     = []
    cur         = data_start
    for cf in compressed_frames:
        offsets.append(cur)
        cur += len(cf)
    offsets.append(cur)  # sentinel = end of file

    buf = bytearray()

    # File header (6 bytes)
    buf += struct.pack('<H', n)      # num_frames
    buf += struct.pack('<H', 0)      # x_delta
    buf += struct.pack('<H', 0)      # y_delta
    # Dimensions entry (8 bytes): width, height, 4 zero bytes
    buf += struct.pack('<H', width)
    buf += struct.pack('<H', height)
    buf += b'\x00\x00\x00\x00'      # padding (total dims entry = 8 bytes)

    # Frame index entries
    for i, off in enumerate(offsets[:-1]):
        buf.append(off & 0xFF)
        buf.append((off >> 8) & 0xFF)
        buf.append((off >> 16) & 0xFF)
        buf.append(0x80)             # LCW flag
        buf += b'\x00\x00\x00\x00'  # ref_offset(3) + ref_flags(1)

    # Sentinel
    sentinel = offsets[-1]
    buf.append(sentinel & 0xFF)
    buf.append((sentinel >> 8) & 0xFF)
    buf.append((sentinel >> 16) & 0xFF)
    buf.append(0x00)
    buf += b'\x00\x00\x00\x00'

    # Trailing zero entry (required by OpenRA's ShpTD loader)
    buf += b'\x00\x00\x00\x00\x00\x00\x00\x00'

    # Frame data
    for cf in compressed_frames:
        buf += cf

    with open(filename, 'wb') as f:
        f.write(buf)
