"""The "Enhanced blacks for LCD screens" option: what it changes and how it's explained."""
from __future__ import annotations

LABEL = "Enhanced blacks for LCD screens"
SHADOWS = -3  # on the Shadows scale (-10..10): clearly deeper near-blacks, little detail lost
INFO_TITLE = "How enhanced blacks works"


def info_text() -> str:
    return (
        "An LCD can't switch its backlight off for a black pixel, so a little light always leaks through. "
        "No software can stop that. This option gets as close as it can:\n\n"
        "•  It darkens the tones just above black, so the leaking light is less noticeable and dark scenes "
        "look deeper. The very darkest shadow details sink into black a bit sooner.\n\n"
        "•  If the monitor lets the app change its black level and it's set high, it goes back to the middle "
        "of its range. That's usually where black is darkest without hiding detail.\n\n"
        "•  Nothing is kept until you confirm it, and you can always go back.\n\n"
        "Other things that help: turn the brightness down to what your room needs, because the leak gets "
        "brighter with the backlight. Use the Blacks step in Match screens to check with a test pattern. "
        "If black still looks dark gray, the PC may be sending limited-range video. Set it to Full in Intel "
        "Graphics Command Center, NVIDIA Control Panel or AMD Software."
    )


def black_level_target(current: int, maximum: int) -> int:
    """Bring a raised monitor black level back to the middle of its range; never raise it."""
    return min(current, round(maximum / 2))
