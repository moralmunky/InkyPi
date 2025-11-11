import requests
from PIL import Image, ImageEnhance, ImageOps, ImageFilter
from io import BytesIO
import os
import logging
import hashlib
import tempfile
import subprocess

logger = logging.getLogger(__name__)

def get_image(image_url):
    response = requests.get(image_url)
    img = None
    if 200 <= response.status_code < 300 or response.status_code == 304:
        img = Image.open(BytesIO(response.content))
    else:
        logger.error(f"Received non-200 response from {image_url}: status_code: {response.status_code}")
    return img

def change_orientation(image, orientation, inverted=False):
    if orientation == 'horizontal':
        angle = 0
    elif orientation == 'vertical':
        angle = 90

    if inverted:
        angle = (angle + 180) % 360

    return image.rotate(angle, expand=1)

def resize_image(image, desired_size, image_settings=[]):
    img_width, img_height = image.size
    desired_width, desired_height = desired_size
    desired_width, desired_height = int(desired_width), int(desired_height)

    img_ratio = img_width / img_height
    desired_ratio = desired_width / desired_height

    keep_width = "keep-width" in image_settings

    x_offset, y_offset = 0,0
    new_width, new_height = img_width,img_height
    # Step 1: Determine crop dimensions
    desired_ratio = desired_width / desired_height
    if img_ratio > desired_ratio:
        # Image is wider than desired aspect ratio
        new_width = int(img_height * desired_ratio)
        if not keep_width:
            x_offset = (img_width - new_width) // 2
    else:
        # Image is taller than desired aspect ratio
        new_height = int(img_width / desired_ratio)
        if not keep_width:
            y_offset = (img_height - new_height) // 2

    # Step 2: Crop the image
    image = image.crop((x_offset, y_offset, x_offset + new_width, y_offset + new_height))

    # Step 3: Resize to the exact desired dimensions (if necessary)
    return image.resize((desired_width, desired_height), Image.LANCZOS)

def _clamp8(value):
    return max(0, min(255, int(value)))


def _apply_vibrance(img, amount):
    if abs(amount) < 1e-3:
        return img

    hsv = img.convert("HSV")
    h_channel, s_channel, v_channel = hsv.split()
    if amount >= 0:
        lut = [_clamp8(i + (255 - i) * amount) for i in range(256)]
    else:
        lut = [_clamp8(i + i * amount) for i in range(256)]
    s_channel = s_channel.point(lut)
    return Image.merge("HSV", (h_channel, s_channel, v_channel)).convert(img.mode)


def _apply_temperature_tint(img, temperature, tint):
    result = img

    if abs(temperature) > 1e-3:
        strength = min(0.5, abs(temperature) * 0.2)
        warm_color = (255, 200, 140)
        cool_color = (130, 190, 255)
        overlay_color = warm_color if temperature > 0 else cool_color
        overlay = Image.new("RGB", img.size, overlay_color)
        result = Image.blend(result, overlay, strength)

    if abs(tint) > 1e-3:
        strength = min(0.5, abs(tint) * 0.2)
        magenta = (255, 160, 210)
        green = (180, 255, 190)
        overlay_color = magenta if tint > 0 else green
        overlay = Image.new("RGB", img.size, overlay_color)
        result = Image.blend(result, overlay, strength)

    return result


def _apply_clarity(img, clarity):
    if clarity <= 0:
        return img
    radius = 1.0 + (clarity * 2.0)
    percent = 150 + int(clarity * 150)
    return img.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=2))


def apply_image_enhancement(img, image_settings=None):
    image_settings = image_settings or {}

    # Apply Brightness
    img = ImageEnhance.Brightness(img).enhance(image_settings.get("brightness", 1.0))

    # Apply Contrast
    img = ImageEnhance.Contrast(img).enhance(image_settings.get("contrast", 1.0))

    # Apply Saturation (Color)
    img = ImageEnhance.Color(img).enhance(image_settings.get("saturation", 1.0))

    # Apply Vibrance (selective saturation)
    img = _apply_vibrance(img, image_settings.get("vibrance", 0.0))

    # Apply Sharpness
    img = ImageEnhance.Sharpness(img).enhance(image_settings.get("sharpness", 1.0))

    # Apply Gamma
    gamma = image_settings.get("gamma", 1.0)
    if abs(gamma - 1.0) > 1e-3:
        img = ImageOps.gamma(img, gamma)

    # Apply temperature/tint shifts
    img = _apply_temperature_tint(
        img,
        image_settings.get("temperature", 0.0),
        image_settings.get("tint", 0.0),
    )

    # Apply clarity (local contrast/sharpen)
    img = _apply_clarity(img, image_settings.get("clarity", 0.0))

    # Apply denoise/blur
    denoise = image_settings.get("denoise", 0.0)
    if denoise > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=denoise))

    # Posterize to limit tones
    posterize_bits = image_settings.get("posterize_bits")
    if posterize_bits:
        bits = max(1, min(8, int(posterize_bits)))
        img = ImageOps.posterize(img, bits)

    return img

def compute_image_hash(image):
    """Compute SHA-256 hash of an image."""
    image = image.convert("RGB")
    img_bytes = image.tobytes()
    return hashlib.sha256(img_bytes).hexdigest()

def take_screenshot_html(html_str, dimensions, timeout_ms=None):
    image = None
    try:
        # Create a temporary HTML file
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as html_file:
            html_file.write(html_str.encode("utf-8"))
            html_file_path = html_file.name

        image = take_screenshot(html_file_path, dimensions, timeout_ms)

        # Remove html file
        os.remove(html_file_path)

    except Exception as e:
        logger.error(f"Failed to take screenshot: {str(e)}")

    return image

def take_screenshot(target, dimensions, timeout_ms=None):
    image = None
    try:
        # Create a temporary output file for the screenshot
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as img_file:
            img_file_path = img_file.name

        command = [
            "chromium-headless-shell",
            target,
            "--headless",
            f"--screenshot={img_file_path}",
            f"--window-size={dimensions[0]},{dimensions[1]}",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--use-gl=swiftshader",
            "--hide-scrollbars",
            "--in-process-gpu",
            "--js-flags=--jitless",
            "--disable-zero-copy",
            "--disable-gpu-memory-buffer-compositor-resources",
            "--disable-extensions",
            "--disable-plugins",
            "--mute-audio",
            "--no-sandbox"
        ]
        if timeout_ms:
            command.append(f"--timeout={timeout_ms}")
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Check if the process failed or the output file is missing
        if result.returncode != 0 or not os.path.exists(img_file_path):
            logger.error("Failed to take screenshot:")
            logger.error(result.stderr.decode('utf-8'))
            return None

        # Load the image using PIL
        with Image.open(img_file_path) as img:
            image = img.copy()

        # Remove image files
        os.remove(img_file_path)

    except Exception as e:
        logger.error(f"Failed to take screenshot: {str(e)}")

    return image

def pad_image_blur(img: Image, dimensions: tuple[int, int]) -> Image:
    bkg = ImageOps.fit(img, dimensions)
    bkg = bkg.filter(ImageFilter.BoxBlur(8))
    img = ImageOps.contain(img, dimensions)

    img_size = img.size
    bkg.paste(img, ((dimensions[0] - img_size[0]) // 2, (dimensions[1] - img_size[1]) // 2))
    return bkg
