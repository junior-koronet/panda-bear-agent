"""
Skill: Generate Welcome Image
Creates the personalized welcome image with employee name, job title, and photo.
"""

import os
import io
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skills.base import Skill

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "template.png")
FONT_NAME = os.path.join(BASE_DIR, "Poppins-Light.ttf")
FONT_CARGO = os.path.join(BASE_DIR, "Poppins-Regular.ttf")
IMAGES_DIR = os.path.join(BASE_DIR, "generated_images")
os.makedirs(IMAGES_DIR, exist_ok=True)

CX, CY, R = 707, 1500, 600


class GenerateImageSkill(Skill):
    name = "generate_image"
    description = "Generates a personalized welcome image with the employee's name, job title, and photo."
    category = "content"

    def _run(self, name: str, job_title: str = "", photo_bytes: bytes = None) -> dict:
        name = (name or "").strip()
        job_title = (job_title or "").strip()

        if not os.path.exists(TEMPLATE_PATH):
            return {
                "success": False,
                "error": f"Template not found at {TEMPLATE_PATH}",
            }

        try:
            template = Image.open(TEMPLATE_PATH).convert("RGBA")
            W, H = template.size

            tm_arr = np.array(template)
            for y in range(max(0, CY - R - 5), min(CY + R + 5, H)):
                for x in range(max(0, CX - R - 5), min(CX + R + 5, W)):
                    if ((x - CX) ** 2 + (y - CY) ** 2) ** 0.5 <= R:
                        tm_arr[y, x, 3] = 0
            template_holed = Image.fromarray(tm_arr, "RGBA")

            base = Image.new("RGBA", (W, H), (0, 0, 0, 255))

            if photo_bytes:
                try:
                    photo = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
                    pw, ph = photo.size
                    s = min(pw, ph)
                    photo_sq = photo.crop(((pw - s) // 2, (ph - s) // 2, (pw + s) // 2, (ph + s) // 2))
                    D = R * 2
                    photo_r = photo_sq.resize((D, D), Image.LANCZOS)
                    mask = Image.new("L", (D, D), 0)
                    ImageDraw.Draw(mask).ellipse((0, 0, D - 1, D - 1), fill=255)
                    photo_r.putalpha(mask)
                    base.paste(photo_r, (CX - R, CY - R), photo_r)
                except Exception as e:
                    print(f"[Image] Photo processing error: {e}")

            base.paste(template_holed, (0, 0), template_holed)
            draw = ImageDraw.Draw(base)

            try:
                fn = ImageFont.truetype(FONT_NAME, 82)
                fc = ImageFont.truetype(FONT_CARGO, 58)
            except Exception:
                fn = ImageFont.load_default()
                fc = fn

            bbox = draw.textbbox((0, 0), name, font=fn)
            draw.text(((W - (bbox[2] - bbox[0])) // 2, 264), name, font=fn, fill="white")

            if job_title:
                bbox2 = draw.textbbox((0, 0), job_title, font=fc)
                draw.text(((W - (bbox2[2] - bbox2[0])) // 2, 468), job_title, font=fc, fill="white")

            safe_name = name.replace(" ", "_").lower()
            output_path = os.path.join(IMAGES_DIR, f"welcome_{safe_name}.png")
            base.convert("RGB").save(output_path, quality=95)

            return {
                "result": output_path,
                "imagePath": output_path,
                "hasPhoto": photo_bytes is not None,
                "decision": f"Generated welcome image for {name}",
                "reasoning": (
                    f"Created personalized welcome image with name='{name}', "
                    f"job_title='{job_title}', has_photo={photo_bytes is not None}."
                ),
                "confidence": 1.0,
            }

        except Exception as e:
            return {"success": False, "error": str(e)}
