"""Content style bundles.

A style is a parameter bundle describing what a genre of television output does
to the light in a room. The differentiating axes are dwell time, cut frequency,
brightness band, saturation distribution and colour temperature.

These numbers came from watching a lamp through a curtain, not from theory. Tune
them against that, not against what looks reasonable in a table.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class Style:
    """Parameters for one content style."""

    name: str
    #: Mean seconds between hard cuts. Converted to a per-tick probability so it
    #: stays correct if the tick rate changes.
    mean_cut: float
    #: Brightness percentage band, (low, high).
    bri: tuple[int, int]
    #: Brightness/hue delta magnitude on an ordinary tick.
    drift: int
    #: Brightness/hue delta magnitude on a cut.
    jump: int
    #: Hue band in degrees, (low, high). A band spanning >= 360 is full circle;
    #: a band where low > high wraps through 0 (e.g. (340, 20)).
    hue: tuple[int, int]
    #: Saturation distribution thresholds, (low, medium, high). Ascending.
    #: Most television light is near-white, so the draw is heavily weighted
    #: toward the low band.
    sat: tuple[int, int, int]
    #: Preferred colour temperature band in kelvin, intersected with what the
    #: device actually supports.
    cct: tuple[int, int]

    def __post_init__(self) -> None:
        """Validate the bundle, since a bad one degrades silently otherwise."""
        if self.bri[0] >= self.bri[1]:
            raise ValueError(f"{self.name}: brightness band must ascend")
        if not (self.sat[0] <= self.sat[1] <= self.sat[2]):
            raise ValueError(f"{self.name}: saturation thresholds must ascend")
        if self.cct[0] >= self.cct[1]:
            raise ValueError(f"{self.name}: colour temperature band must ascend")
        if self.mean_cut <= 0:
            raise ValueError(f"{self.name}: mean_cut must be positive")

    @property
    def hue_is_full_circle(self) -> bool:
        """Whether the hue band covers the whole colour wheel."""
        return (self.hue[1] - self.hue[0]) >= 360

    @property
    def hue_wraps(self) -> bool:
        """Whether the hue band wraps through 0 degrees, e.g. (340, 20)."""
        return not self.hue_is_full_circle and self.hue[0] > self.hue[1]


_STYLE_LIST: Final = (
    # Slow, dim, warm amber. Long scenes, occasional dramatic swing.
    Style("film", 16, (14, 55), 4, 26, (12, 45), (8, 22, 45), (2700, 3400)),
    # Big warm swings, frequent cuts, orange explosion flashes.
    Style("action", 6, (20, 90), 9, 40, (8, 45), (14, 35, 70), (2700, 4200)),
    # Studio-lit, cool and near-constant, with camera-angle cuts. The least
    # colourful style: a steady cool-white wash with the faintest blue tint.
    Style("news", 15, (55, 75), 2, 10, (200, 235), (5, 12, 24), (5000, 6500)),
    # Bright, steady, green cast from the pitch.
    Style("sport", 9, (45, 80), 4, 16, (95, 150), (14, 30, 55), (4500, 6000)),
    # Saturated and continuously moving across the whole wheel, but few cuts.
    Style("game", 20, (25, 80), 7, 30, (0, 360), (35, 60, 92), (3000, 5000)),
    # Dim and sporadic, warm. What is on at 01:00.
    Style("latenight", 26, (8, 30), 3, 14, (12, 45), (8, 20, 40), (2700, 3200)),
    # Bright, garish, relentless, full-wheel. Engaged by the ad-break state
    # machine rather than chosen directly.
    Style("ads", 3, (50, 95), 12, 45, (0, 360), (40, 68, 95), (3500, 5500)),
)

STYLES: Final[MappingProxyType[str, Style]] = MappingProxyType(
    {style.name: style for style in _STYLE_LIST}
)

#: Styles a user can pick. `ads` is excluded: it is driven by the ad-break state
#: machine, and a permanent ad break is not a thing anyone wants.
SELECTABLE_STYLES: Final = tuple(name for name in STYLES if name != "ads")


def get_style(name: str) -> Style:
    """Return a style by name, falling back to the default rather than raising.

    The engine must never die because a style name went missing; a wrong-looking
    lamp beats a stopped one when the point is to look occupied.
    """
    return STYLES.get(name, STYLES["news"])
