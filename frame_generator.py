"""
Frame generation for C&C SHP sprites.

TWO generation modes:

  STANDING (infantry, standing units):
    The unit is always upright.  Facings are derived by compressing
    the source frame horizontally so it appears narrower as it "turns
    away" from the viewer — exactly how C&C infantry sprites look.

        S  → full-width front view   (scale 1.0)
        SE → slightly narrower       (scale 0.88)
        E  → side profile            (scale 0.60)
        NE → narrow + flipped        (scale 0.50, shows back-right)
        N  → horizontally flipped    (back view)
        SW/W/NW = mirrors of SE/E/NE

  TOP-DOWN (vehicles, aircraft, top-view sprites):
    Proper clockwise rotation.  Works correctly because a top-down
    sprite really does look right when rotated.
    SW/W/NW are mirrors of SE/E/NE for clean results.

C&C facing order (both modes): N NE E SE S SW W NW  (indices 0-7)
"""

import math

# ── Animation / building class tables ──────────────────────────────────────

ANIMATION_CLASSES = {
    "Idle":      {"facings": 8, "description": "Unit standing still (all 8 directions)"},
    "Walk":      {"facings": 8, "description": "Unit walking (all 8 directions, multi-frame)"},
    "Fire":      {"facings": 8, "description": "Unit firing weapon (all 8 directions)"},
    "Die":       {"facings": 1, "description": "Death animation (single direction, multi-frame)"},
    "Prostrate": {"facings": 1, "description": "Unit lying prone (single direction)"},
    "Deploy":    {"facings": 1, "description": "Unit deploying (single direction)"},
    "Guard":     {"facings": 8, "description": "Unit on guard (all 8 directions)"},
    "Swim":      {"facings": 8, "description": "Amphibious movement (all 8 directions)"},
}

FACING_NAMES_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

BUILDING_CLASSES = {
    "Intact":    {"facings": 1, "description": "Building undamaged"},
    "Damaged":   {"facings": 1, "description": "Building lightly damaged"},
    "Critical":  {"facings": 1, "description": "Building heavily damaged"},
    "Destroyed": {"facings": 1, "description": "Building rubble/destroyed"},
    "Active":    {"facings": 1, "description": "Building active animation (power plants, etc.)"},
}

# Clockwise angle (degrees) for each facing index
FACING_ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]


# ── Primitive pixel operations ──────────────────────────────────────────────

def flip_frame_horizontal(pixels, width, height):
    """Mirror a frame left-right."""
    result = []
    for y in range(height):
        row = pixels[y * width:(y + 1) * width]
        result.extend(reversed(row))
    return result


def compress_horizontal(pixels, width, height, scale, cx_offset=0):
    """
    Compress (or expand) the sprite horizontally around the frame centre.

      scale < 1.0  →  narrower  (e.g. 0.60 = 60% as wide, sides transparent)
      scale = 1.0  →  unchanged
      scale > 1.0  →  wider  (rarely needed here)

    Uses inverse nearest-neighbour mapping so palette indices are preserved
    exactly — no colour blending, no palette re-quantisation.
    """
    result    = [0] * (width * height)
    center_x  = (width - 1) / 2.0 + cx_offset

    for y in range(height):
        for x_out in range(width):
            # Inverse map: where in the source does this output pixel come from?
            x_src_f = center_x + (x_out - center_x) / scale
            x_src_i = int(x_src_f + 0.5)
            if 0 <= x_src_i < width:
                result[y * width + x_out] = pixels[y * width + x_src_i]
            # else stays 0 (transparent — the unit is narrower here)

    return result


def rotate_frame_indexed(pixels, width, height, angle_deg):
    """
    Rotate clockwise by angle_deg using nearest-neighbour on palette indices.
    No RGB conversion — palette indices (including remap colours) are exact.
    Used only for TOP-DOWN sprites where real rotation makes sense.
    """
    if angle_deg % 360 == 0:
        return list(pixels)

    rad   = math.radians(angle_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    cx    = (width  - 1) / 2.0
    cy    = (height - 1) / 2.0

    result = [0] * (width * height)
    for y in range(height):
        for x in range(width):
            dx    = x - cx
            dy    = y - cy
            sx    = cos_a * dx + sin_a * dy + cx
            sy    = -sin_a * dx + cos_a * dy + cy
            sx_i  = int(sx + 0.5)
            sy_i  = int(sy + 0.5)
            if 0 <= sx_i < width and 0 <= sy_i < height:
                result[y * width + x] = pixels[sy_i * width + sx_i]
    return result


# ── Main facing generators ──────────────────────────────────────────────────

def generate_8_facings_standing(first_frame_pixels, width, height, palette, first_facing=4):
    """
    Generate 8 facings for a STANDING sprite (infantry, standing figures).

    The unit stays upright in every frame.  Horizontal compression
    simulates the perspective narrowing you see in real C&C sprites
    as the unit turns away from the camera.

    Compression scale per facing (relative to a full front view):
        S   1.00  full front
        SE  0.88  slight turn
        E   0.60  side profile
        NE  0.50  back-right (uses flipped source)
        N   1.00  back view  (horizontal flip of front)
        SW/W/NW = horizontal mirrors of SE/E/NE

    first_facing: which direction the drawn frame is facing.
    If not S (4), the source is first rotated to an equivalent S
    so the compression scales are always correct.
    """
    # --- Normalise source to an S-equivalent ---
    # (For S reference: no change.  For other facings we nudge toward
    #  an approximate S orientation using rotation — only used once,
    #  and only for cardinal/diagonal starts other than S.)
    if first_facing == 4:
        source = list(first_frame_pixels)
    else:
        # Rotate source so it points "toward viewer" (≈ S direction)
        delta = (FACING_ANGLES[4] - FACING_ANGLES[first_facing]) % 360
        source = rotate_frame_indexed(first_frame_pixels, width, height, delta)

    source_flip = flip_frame_horizontal(source, width, height)

    facings = [None] * 8

    # Right-side / front facings (all derived from source = S-normalised)
    facings[4] = list(source)                                            # S
    facings[3] = compress_horizontal(source,      width, height, 0.88)  # SE
    facings[2] = compress_horizontal(source,      width, height, 0.60)  # E
    facings[1] = compress_horizontal(source_flip, width, height, 0.50)  # NE
    facings[0] = list(source_flip)                                       # N

    # Left-side: mirrors of right-side
    facings[5] = flip_frame_horizontal(facings[3], width, height)  # SW = mirror of SE
    facings[6] = flip_frame_horizontal(facings[2], width, height)  # W  = mirror of E
    facings[7] = flip_frame_horizontal(facings[1], width, height)  # NW = mirror of NE

    # Restore the original drawn frame to its correct slot (unmodified)
    facings[first_facing] = list(first_frame_pixels)

    return facings


def generate_8_facings_topdown(first_frame_pixels, width, height, palette, first_facing=0):
    """
    Generate 8 facings for a TOP-DOWN sprite (vehicles, aircraft).

    Uses proper clockwise rotation — correct because a top-down sprite
    genuinely looks right when rotated.
    SW/W/NW are mirrors of SE/E/NE for clean symmetry.
    """
    base_angle = FACING_ANGLES[first_facing]
    facings    = [None] * 8

    for i in [0, 1, 2, 3, 4]:
        delta = (FACING_ANGLES[i] - base_angle) % 360
        if delta == 0:
            facings[i] = list(first_frame_pixels)
        else:
            facings[i] = rotate_frame_indexed(first_frame_pixels, width, height, delta)

    facings[5] = flip_frame_horizontal(facings[3], width, height)
    facings[6] = flip_frame_horizontal(facings[2], width, height)
    facings[7] = flip_frame_horizontal(facings[1], width, height)

    return facings


def generate_8_facings(first_frame_pixels, width, height, palette,
                       first_facing=4, standing=True):
    """
    Unified entry point.  Dispatches to standing or top-down generator.
    """
    if standing:
        return generate_8_facings_standing(
            first_frame_pixels, width, height, palette, first_facing)
    else:
        return generate_8_facings_topdown(
            first_frame_pixels, width, height, palette, first_facing)


def generate_building_frames(first_frame_pixels, width, height, num_frames=1):
    """Duplicate the first frame for each building damage state."""
    return [list(first_frame_pixels) for _ in range(num_frames)]
