"""
C&C Palette definitions loaded directly from OS SHP Builder palette files.
Each palette is a list of 256 (R, G, B) tuples, values 0-255.
VGA .pal files store each channel as 0-63; we multiply by 4.
"""

import os

_PAL_BASE = r'C:\Program Files\CnCTools\OS SHP Builder\Palettes'

def _load_pal(rel_path):
    """Load a 768-byte VGA palette file -> list of 256 (R,G,B) tuples."""
    path = os.path.join(_PAL_BASE, rel_path)
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if len(data) >= 768:
            return [(min(255, data[i*3]*4),
                     min(255, data[i*3+1]*4),
                     min(255, data[i*3+2]*4))
                    for i in range(256)]
    except FileNotFoundError:
        pass
    # Fallback: simple generated palette
    return _make_fallback()

def _make_fallback():
    colors = [(0, 0, 0)]
    for i in range(1, 256):
        r = (i & 0b11100000)
        g = (i & 0b00011100) << 3
        b = (i & 0b00000011) << 6
        colors.append((r, g, b))
    return colors

TD_PALETTE  = _load_pal(r'TD\temperat.pal')
RA1_PALETTE = _load_pal(r'RA1\temperat.pal')
TS_PALETTE  = _load_pal(r'TS\unittem.pal')
RA2_PALETTE = _load_pal(r'RA2\unittem.pal')
YR_PALETTE  = _load_pal(r'YR\unitdes.pal')

PALETTES = {
    "Tiberian Dawn":  TD_PALETTE,
    "Red Alert 1":    RA1_PALETTE,
    "Tiberian Sun":   TS_PALETTE,
    "Red Alert 2":    RA2_PALETTE,
    "Yuri's Revenge": YR_PALETTE,
}

PALETTE_NAMES = list(PALETTES.keys())

SPECIAL_INDICES = {
    0:  "Transparent",
    4:  "Shadow",
    80: "Remap (darkest)",
    95: "Remap (lightest)",
}

# Remap range
REMAP_START = 80
REMAP_END   = 96
