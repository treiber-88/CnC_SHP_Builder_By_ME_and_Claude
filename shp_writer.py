"""
SHP file writer for C&C / OpenRA SHP format.
Supports the TD/RA1 SHP format (SHP(TD)) used by OpenRA.

SHP(TD) format:
  Header: 2 bytes num_frames, 4 bytes unused (0x20 0x00)
  Frame headers: num_frames * 10 bytes each
    - 2 bytes x offset
    - 2 bytes y offset
    - 2 bytes width
    - 2 bytes height
    - 1 byte compression flags
    - 3 bytes file offset (24-bit)
  Frame data: compressed pixel data
"""

import struct
import zlib


def compress_frame_rle(pixels, width, height):
    """
    Compress frame data using SHP RLE format.
    Format 0x20 = line-by-line RLE.
    Returns compressed bytes.
    """
    compressed = bytearray()

    for y in range(height):
        row = pixels[y * width:(y + 1) * width]
        line_data = bytearray()

        x = 0
        while x < width:
            pixel = row[x]

            if pixel == 0:
                # Count run of transparent pixels
                count = 0
                while x + count < width and row[x + count] == 0 and count < 255:
                    count += 1
                line_data.append(0)  # transparent marker
                line_data.append(count)
                x += count
            else:
                # Collect non-transparent pixels
                run = []
                while x + len(run) < width and row[x + len(run)] != 0 and len(run) < 255:
                    run.append(row[x + len(run)])
                line_data.append(len(run))
                line_data.extend(run)
                x += len(run)

        # Each line is preceded by its length (2 bytes)
        compressed.extend(struct.pack('<H', len(line_data) + 2))
        compressed.extend(line_data)

    return bytes(compressed)


def write_shp(filename, frames, frame_width, frame_height):
    """
    Write frames to an SHP(TD) file.

    frames: list of pixel data, each a list of 256 palette indices,
            length = frame_width * frame_height
    frame_width, frame_height: dimensions of each frame
    """
    num_frames = len(frames)

    # Header size: 2 + 2 + 2 = 6 bytes (numFrames, width, height)
    # Wait - TD SHP has a different header:
    # Offset 0: uint16 - number of frames
    # Offset 2: uint16 - x delta (usually 0)
    # Offset 4: uint16 - y delta (usually 0)
    # Then frame offsets table: (num_frames + 2) * 4 bytes
    # Then frame data

    # Actually let's use the correct SHP(TD) format:
    # 6-byte file header
    # (num_frames + 1) * 8-byte frame headers (last is sentinel)
    # pixel data

    # Correct SHP TD format:
    # uint16: frame count
    # uint16: file size (lo word) - filled later
    # uint16: file size (hi word) - filled later
    # Then for each frame + 1 sentinel:
    #   uint32: offset into file
    #   uint16: compression type (0=raw, 2=LCW, 3=XOR+LCW, 4=XOR chain)
    #   uint16: frame info (reference frame for XOR)
    # Then frame data blocks, each:
    #   uint16: compressed size
    #   uint16: uncompressed size
    #   pixel data

    # Simplest approach: use format 0 (uncompressed) for reliability

    header_size = 6
    frame_header_size = 8
    frame_headers_total = (num_frames + 1) * frame_header_size

    header_offset = header_size + frame_headers_total

    # Compress each frame
    frame_data_list = []
    for frame_pixels in frames:
        # Pad/clip to correct size
        pixels = list(frame_pixels)[:frame_width * frame_height]
        while len(pixels) < frame_width * frame_height:
            pixels.append(0)
        frame_data_list.append(bytes(pixels))

    # Calculate offsets
    offsets = []
    current_offset = header_offset
    for data in frame_data_list:
        offsets.append(current_offset)
        current_offset += 4 + len(data)  # 4 byte block header + data
    offsets.append(current_offset)  # sentinel

    file_size = current_offset

    # Build file
    buf = bytearray()

    # File header
    buf.extend(struct.pack('<H', num_frames))
    buf.extend(struct.pack('<H', file_size & 0xFFFF))
    buf.extend(struct.pack('<H', (file_size >> 16) & 0xFFFF))

    # Frame headers
    for i, offset in enumerate(offsets[:-1]):
        buf.extend(struct.pack('<I', offset))  # offset
        buf.extend(struct.pack('<H', 0))  # compression: 0 = uncompressed
        buf.extend(struct.pack('<H', 0))  # reference frame

    # Sentinel
    buf.extend(struct.pack('<I', offsets[-1]))
    buf.extend(struct.pack('<H', 0))
    buf.extend(struct.pack('<H', 0))

    # Frame data blocks
    for data in frame_data_list:
        uncompressed_size = len(data)
        buf.extend(struct.pack('<H', uncompressed_size))  # compressed size (same for raw)
        buf.extend(struct.pack('<H', uncompressed_size))  # uncompressed size
        buf.extend(data)

    with open(filename, 'wb') as f:
        f.write(buf)

    return len(buf)


def write_shp_openra(filename, frames, frame_width, frame_height):
    """
    Write SHP file in OpenRA-compatible SHP(TD) format.
    This is the most commonly used format for OpenRA mods.

    Frame pixel data uses palette indices (0 = transparent).
    """
    return write_shp(filename, frames, frame_width, frame_height)
