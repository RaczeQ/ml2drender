"""The attribution in the corner of the axes: a matplotlib annotation, imported when one is added."""

from typing import TYPE_CHECKING, cast

from matplotlib import font_manager, rcParams
from matplotlib.text import Annotation

if TYPE_CHECKING:
    from matplotlib.backend_bases import RendererBase

# The attribution box's padding, in font sizes.
PAD = 0.2
# Fonts of Chinese, Japanese and Korean (macOS, Windows, Linux), which matplotlib's default lacks, for attributions
# such as GSI's: those installed back up the default font.
_CJK_FONTS = ["Hiragino Sans", "Yu Gothic", "Microsoft YaHei", "Malgun Gothic", "Noto Sans CJK JP"]


def font_family() -> list[str]:
    """matplotlib's default font family, then the fonts of :data:`_CJK_FONTS` that are installed."""
    installed = {font.name for font in font_manager.fontManager.ttflist}
    return [*rcParams["font.family"], *(name for name in _CJK_FONTS if name in installed)]


class Attribution(Annotation):
    """An annotation whose words are wrapped to the width of its axes whenever it is drawn."""

    def draw(self, renderer: "RendererBase") -> None:
        if self.axes is not None:
            font = self.get_fontproperties()
            # The text and the padding of its box on both sides.
            width = self.axes.get_window_extent(renderer).width
            width -= cast(float, renderer.points_to_pixels(2 * PAD * font.get_size_in_points()))
            lines: list[str] = []
            for word in self.get_text().split():
                if lines and renderer.get_text_width_height_descent(f"{lines[-1]} {word}", font, False)[0] <= width:
                    lines[-1] += f" {word}"
                else:
                    lines.append(word)
            self.set_text("\n".join(lines))
        super().draw(renderer)
