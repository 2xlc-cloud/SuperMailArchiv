"""Render the blue envelope icon for Tk and the Windows executable."""

import struct
import zlib


def inside_rounded(x, y, left, top, right, bottom, radius):
    if not (left <= x <= right and top <= y <= bottom):
        return False
    center_x = min(max(x, left + radius), right - radius)
    center_y = min(max(y, top + radius), bottom - radius)
    return (x - center_x) ** 2 + (y - center_y) ** 2 <= radius ** 2


def near_segment(x, y, start, end, radius):
    dx, dy = end[0] - start[0], end[1] - start[1]
    projection = ((x - start[0]) * dx + (y - start[1]) * dy) / (dx * dx + dy * dy)
    fraction = max(0, min(1, projection))
    return (x - start[0] - fraction * dx) ** 2 + (y - start[1] - fraction * dy) ** 2 <= radius ** 2


# Blue is the normal icon; orange marks a running mail sync.
COLORS = {
    "blue": {"flap": (26, 93, 196), "fold": (160, 197, 248), "shadow": (17, 66, 155),
             "top": (50, 134, 246), "bottom": (14, 69, 184)},
    "orange": {"flap": (204, 98, 0), "fold": (252, 204, 150), "shadow": (168, 72, 0),
               "top": (255, 168, 38), "bottom": (226, 102, 0)},
}


def icon_color(x, y, colors=COLORS["blue"]):
    if not inside_rounded(x, y, 1, 1, 31, 31, 7):
        return None
    if inside_rounded(x, y, 5.5, 8.5, 26.5, 23.5, 2.5):
        if (near_segment(x, y, (6, 9.5), (16, 17.4), 0.8)
                or near_segment(x, y, (16, 17.4), (26, 9.5), 0.8)):
            return colors["flap"]
        if (near_segment(x, y, (6.5, 23), (12.5, 17.5), 0.55)
                or near_segment(x, y, (25.5, 23), (19.5, 17.5), 0.55)):
            return colors["fold"]
        return (255, 255, 255)
    if inside_rounded(x, y, 5.5, 9.6, 26.5, 24.6, 2.5):
        return colors["shadow"]
    fraction = (y - 1) / 30
    return tuple(round(top + (bottom - top) * fraction) for top, bottom in zip(colors["top"], colors["bottom"]))


def png_icon(size, color="blue"):
    samples = 3 if size <= 64 else 2
    unit = 32 / size
    rows = bytearray()
    for row in range(size):
        rows.append(0)  # PNG scanline with no filter.
        for column in range(size):
            colors = [
                icon_color((column + (dx + 0.5) / samples) * unit,
                           (row + (dy + 0.5) / samples) * unit, COLORS[color])
                for dy in range(samples) for dx in range(samples)
            ]
            visible = [color for color in colors if color is not None]
            if visible:
                rows.extend(round(sum(channel) / len(visible)) for channel in zip(*visible))
                rows.append(round(255 * len(visible) / len(colors)))
            else:
                rows.extend((0, 0, 0, 0))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def ico_icon():
    sizes = (16, 24, 32, 48, 64, 128, 256)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    directory = bytearray()
    images = bytearray()
    offset = len(header) + 16 * len(sizes)
    for size in sizes:
        png = png_icon(size)
        dimension = size % 256  # ICO encodes a dimension of 256 as zero.
        directory.extend(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(png), offset))
        images.extend(png)
        offset += len(png)
    return header + directory + images
