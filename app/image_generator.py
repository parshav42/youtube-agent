import hashlib
import random
from pathlib import Path

from PIL import Image, ImageDraw

IMAGE_WIDTH = 1280
IMAGE_HEIGHT = 720

_SCENES = {
    "ocean": ((13, 57, 91), (73, 158, 177), (238, 190, 110)),
    "forest": ((20, 54, 48), (82, 125, 83), (221, 190, 112)),
    "city": ((33, 39, 78), (154, 90, 106), (255, 190, 111)),
    "space": ((8, 12, 38), (49, 45, 98), (190, 174, 255)),
    "desert": ((111, 63, 48), (225, 151, 87), (255, 220, 145)),
    "mountain": ((37, 59, 91), (119, 148, 166), (255, 198, 129)),
}

_SCENE_WORDS = {
    "ocean": {"ocean", "sea", "beach", "wave", "waves", "underwater"},
    "forest": {"forest", "woods", "tree", "trees", "jungle"},
    "city": {"city", "urban", "street", "building", "buildings"},
    "space": {"space", "planet", "galaxy", "stars", "universe"},
    "desert": {"desert", "sand", "dune", "dunes"},
}


def _scene_for_prompt(prompt: str) -> str:
    words = set(prompt.lower().split())
    for scene, scene_words in _SCENE_WORDS.items():
        if words & scene_words:
            return scene
    return "mountain"


def _draw_dunes(
    draw: ImageDraw.ImageDraw,
    rng: random.Random,
    width: int,
    height: int,
    colors: tuple[tuple[int, int, int], ...],
) -> None:
    horizon = int(height * 0.62)
    for index, color in enumerate(colors):
        baseline = horizon + index * height // 10
        points = [(0, height)]
        points.extend(
            (x, baseline + rng.randint(-height // 18, height // 18))
            for x in range(0, width + 1, max(1, width // 16))
        )
        points.append((width, height))
        draw.polygon(points, fill=color)


def _draw_city(
    draw: ImageDraw.ImageDraw,
    rng: random.Random,
    width: int,
    height: int,
    color: tuple[int, int, int],
    light: tuple[int, int, int],
) -> None:
    baseline = int(height * 0.82)
    x = 0
    while x < width:
        building_width = rng.randint(max(30, width // 28), max(50, width // 12))
        building_height = rng.randint(height // 8, height // 2)
        top = baseline - building_height
        draw.rectangle((x, top, x + building_width, height), fill=color)
        window_size = max(4, width // 100)
        for window_y in range(top + window_size, baseline - window_size, window_size * 3):
            for window_x in range(x + window_size, x + building_width - window_size, window_size * 3):
                if rng.random() > 0.35:
                    draw.rectangle(
                        (window_x, window_y, window_x + window_size, window_y + window_size),
                        fill=light,
                    )
        x += building_width + rng.randint(2, max(4, width // 100))


def create_image(
    prompt: str,
    output_path: Path,
    width: int = IMAGE_WIDTH,
    height: int = IMAGE_HEIGHT,
) -> Path:
    """Create a deterministic, prompt-themed illustration without an image API."""
    if not prompt.strip():
        raise ValueError("Image prompt cannot be empty.")
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive.")

    scene = _scene_for_prompt(prompt)
    top_color, bottom_color, accent = _SCENES[scene]
    seed = int.from_bytes(hashlib.sha256(prompt.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)

    for y in range(height):
        amount = y / max(1, height - 1)
        color = tuple(
            round(start + (end - start) * amount)
            for start, end in zip(top_color, bottom_color)
        )
        draw.line((0, y, width, y), fill=color)

    sun_x = rng.randint(width // 5, width * 4 // 5)
    sun_y = height * (0.32 if scene != "space" else 0.42)
    sun_radius = max(18, min(width, height) // 12)
    draw.ellipse(
        (sun_x - sun_radius, sun_y - sun_radius, sun_x + sun_radius, sun_y + sun_radius),
        fill=accent,
    )

    if scene == "space":
        for _ in range(max(20, width * height // 12000)):
            x = rng.randrange(width)
            y = rng.randrange(height)
            radius = rng.choice((1, 1, 2, 3))
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(240, 235, 255))
    elif scene == "ocean":
        for index, color in enumerate(((24, 105, 139), (16, 79, 119), (11, 51, 84))):
            baseline = int(height * (0.58 + index * 0.12))
            points = [(0, height)]
            points.extend(
                (x, baseline + round(height * 0.025 * ((x // max(1, width // 12)) % 2)))
                for x in range(0, width + 1, max(1, width // 12))
            )
            points.append((width, height))
            draw.polygon(points, fill=color)
    elif scene == "forest":
        _draw_dunes(draw, rng, width, height, ((42, 91, 67), (24, 67, 55), (13, 43, 42)))
        for x in range(0, width, max(1, width // 14)):
            tree_height = rng.randint(height // 5, height // 2)
            base = int(height * 0.88)
            draw.polygon(
                ((x, base), (x + width // 28, base - tree_height), (x + width // 14, base)),
                fill=(11, 39, 39),
            )
    elif scene == "city":
        _draw_city(draw, rng, width, height, (31, 39, 65), (255, 205, 127))
    elif scene in {"desert", "mountain"}:
        if scene == "desert":
            _draw_dunes(draw, rng, width, height, ((193, 111, 66), (152, 79, 58), (105, 53, 49)))
        else:
            for index, color in enumerate(((103, 133, 149), (65, 94, 119), (37, 62, 91))):
                baseline = int(height * (0.66 + index * 0.1))
                points = [(0, height)]
                points.extend(
                    (x, rng.randint(height // 4, baseline))
                    for x in range(0, width + 1, max(1, width // 12))
                )
                points.append((width, height))
                draw.polygon(points, fill=color)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("Image generation did not create an image file.")
    return output_path
