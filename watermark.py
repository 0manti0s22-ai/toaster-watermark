from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterable

from PIL import Image


class WatermarkError(Exception):
    """Raised when watermark processing fails."""


class WatermarkProcessor:
    """Applies resizing/cropping and a fixed watermark to images."""

    SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
    FORMATS = {
        "square": (1080, 1080),
        "vertical": (1080, 1440),
        "horizontal": (1920, 1080),
    }

    LOGO_PATH = Path("static/logo.png")
    LOGO_MARGIN = 64
    MAX_PREPROCESS_EDGE = 2000

    def __init__(self) -> None:
        self.logo_path = self.LOGO_PATH

    def is_logo_available(self) -> bool:
        return self.logo_path.exists()

    def validate_images(self, image_names: Iterable[str], max_count: int = 50) -> None:
        image_names = list(image_names)
        if not image_names:
            raise WatermarkError("Не выбраны изображения для обработки.")

        if len(image_names) > max_count:
            raise WatermarkError(f"Можно загрузить максимум {max_count} изображений за раз.")

        invalid = [name for name in image_names if Path(name).suffix.lower() not in self.SUPPORTED_EXTENSIONS]
        if invalid:
            raise WatermarkError("Неподдерживаемый формат файла: " + ", ".join(invalid))

    def validate_format(self, format_name: str) -> tuple[int, int]:
        size = self.FORMATS.get(format_name)
        if size is None:
            raise WatermarkError("Выберите корректный формат: Квадрат, Вертикальный или Горизонтальный.")
        return size

    @staticmethod
    def _crop_to_aspect(image: Image.Image, target_width: int, target_height: int) -> Image.Image:
        source_w, source_h = image.size
        target_ratio = target_width / target_height
        source_ratio = source_w / source_h

        if source_ratio > target_ratio:
            new_w = int(source_h * target_ratio)
            new_h = source_h
        else:
            new_w = source_w
            new_h = int(source_w / target_ratio)

        left = (source_w - new_w) // 2
        top = (source_h - new_h) // 2
        right = left + new_w
        bottom = top + new_h

        return image.crop((left, top, right, bottom))

    @staticmethod
    def _fit_to_target(cropped: Image.Image, tw: int, th: int) -> Image.Image:
        """Scale to exact (tw, th); prefer thumbnail (in-place) when downsizing."""
        cw, ch = cropped.size
        target_max = max(tw, th)
        if max(cw, ch) > target_max:
            cropped.thumbnail((tw, th), Image.Resampling.LANCZOS)
        if cropped.size != (tw, th):
            return cropped.resize((tw, th), Image.Resampling.LANCZOS)
        return cropped

    def apply_to_file(self, image_path: str | Path, format_name: str) -> bytes:
        if not self.is_logo_available():
            raise WatermarkError("Файл логотипа static/logo.png не найден на сервере.")

        target_size = self.validate_format(format_name)
        tw, th = target_size

        image_path = Path(image_path)
        suffix = image_path.suffix.lower()
        if suffix not in self.SUPPORTED_EXTENSIONS:
            raise WatermarkError(f"Формат {suffix or 'unknown'} не поддерживается.")

        prepared: Image.Image | None = None
        layer: Image.Image | None = None
        result: Image.Image | None = None

        try:
            with Image.open(image_path) as src:
                work = src.convert("RGBA")
                work.thumbnail(
                    (self.MAX_PREPROCESS_EDGE, self.MAX_PREPROCESS_EDGE),
                    Image.Resampling.LANCZOS,
                )
                cropped = self._crop_to_aspect(work, tw, th)

            prepared = self._fit_to_target(cropped, tw, th)
            if prepared is not cropped:
                cropped.close()

            with Image.open(self.logo_path) as logo_src:
                logo_rgba = logo_src.convert("RGBA")
                layer = Image.new("RGBA", prepared.size, (0, 0, 0, 0))
                layer.paste(logo_rgba, (self.LOGO_MARGIN, self.LOGO_MARGIN), logo_rgba)

            result = Image.alpha_composite(prepared, layer)

            output = BytesIO()
            if suffix in {".jpg", ".jpeg"}:
                if result.mode in ("RGBA", "LA", "P"):
                    rgb = result.convert("RGB")
                    try:
                        rgb.save(output, format="JPEG", quality=95)
                    finally:
                        rgb.close()
                else:
                    result.save(output, format="JPEG", quality=95)
            elif suffix == ".png":
                result.save(output, format="PNG")
            elif suffix == ".webp":
                result.save(output, format="WEBP", quality=95)

            return output.getvalue()
        except OSError as exc:
            raise WatermarkError(
                f"Не удалось обработать файл {image_path.name}. Возможно, файл поврежден."
            ) from exc
        finally:
            for im in (result, layer, prepared):
                if im is not None:
                    try:
                        im.close()
                    except Exception:
                        pass
