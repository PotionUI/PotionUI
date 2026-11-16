import numpy as np
from PIL import Image, ImageFilter


def compute_initial_abcd(mask):
    indices = np.where(mask > 0)
    if len(indices[0]) == 0:
        return 0, mask.shape[0], 0, mask.shape[1]

    a = np.min(indices[0])
    b = np.max(indices[0])
    c = np.min(indices[1])
    d = np.max(indices[1])

    abp = (b + a) // 2
    abm = (b - a) // 2
    cdp = (d + c) // 2
    cdm = (d - c) // 2
    l = int(max(abm, cdm) * 1.15)

    a = abp - l
    b = abp + l + 1
    c = cdp - l
    d = cdp + l + 1

    H, W = mask.shape[:2]
    a = max(0, min(H, a))
    b = max(0, min(H, b))
    c = max(0, min(W, c))
    d = max(0, min(W, d))

    return int(a), int(b), int(c), int(d)


def solve_abcd(mask, a, b, c, d, k=0.618):
    k = float(k)
    assert 0.0 <= k <= 1.0

    H, W = mask.shape[:2]

    if k == 1.0:
        return 0, H, 0, W

    while True:
        if b - a >= H * k and d - c >= W * k:
            break

        add_h = (b - a) < (d - c)
        add_w = not add_h

        if b - a == H:
            add_w = True

        if d - c == W:
            add_h = True

        if add_h:
            a -= 1
            b += 1

        if add_w:
            c -= 1
            d += 1

        a = max(0, min(H, a))
        b = max(0, min(H, b))
        c = max(0, min(W, c))
        d = max(0, min(W, d))

    return int(a), int(b), int(c), int(d)


def get_image_shape_ceil(image):
    H, W = image.shape[:2]
    return max(H, W)


def set_image_shape_ceil(image, max_size):
    H, W = image.shape[:2]
    current_max = max(H, W)

    if current_max <= max_size:
        new_H = (H // 8) * 8
        new_W = (W // 8) * 8
        if new_H == H and new_W == W:
            return image
    else:
        scale = max_size / current_max
        new_H = int(H * scale)
        new_W = int(W * scale)

    new_H = (new_H // 8) * 8
    new_W = (new_W // 8) * 8

    pil_img = Image.fromarray(image)
    pil_img = pil_img.resize((new_W, new_H), Image.LANCZOS)
    return np.array(pil_img)


def resample_image(image, width, height):
    pil_img = Image.fromarray(image)
    pil_img = pil_img.resize((width, height), Image.LANCZOS)
    return np.array(pil_img)


def mask_bbox(mask):
    ys, xs = np.nonzero(mask > 0)
    if len(ys) == 0:
        return None
    return int(ys.min()), int(ys.max()) + 1, int(xs.min()), int(xs.max()) + 1


def _fit_axis(lo, hi, min_len, limit):
    lo = max(0, lo)
    hi = min(limit, hi)
    if hi - lo < min_len:
        need = min_len - (hi - lo)
        lo -= need // 2
        hi += need - need // 2
    if lo < 0:
        hi -= lo
        lo = 0
    if hi > limit:
        lo -= hi - limit
        hi = limit
    return max(0, lo), hi


fit_axis = _fit_axis


def scaled_crop_box(shape, bbox, context, min_side):
    height, width = shape[:2]
    if bbox is None:
        return 0, height, 0, width
    top, bottom, left, right = bbox
    pad = max(1, int(np.ceil(float(context) * max(bottom - top, right - left))))
    top, bottom = _fit_axis(top - pad, bottom + pad, int(min_side), height)
    left, right = _fit_axis(left - pad, right + pad, int(min_side), width)
    return top, bottom, left, right


def snap_to_multiple(value, multiple):
    multiple = max(1, int(multiple))
    return max(multiple, int(value / multiple + 0.5) * multiple)


def working_size(height, width, target_area, multiple):
    scale = (float(target_area) / max(1, height * width)) ** 0.5
    return snap_to_multiple(height * scale, multiple), snap_to_multiple(width * scale, multiple)


def color_correction(inpainted_image, original_image, mask):
    fg = inpainted_image.astype(np.float32)
    bg = original_image.astype(np.float32)

    if mask.ndim == 2:
        mask = mask[:, :, None]

    w = mask.astype(np.float32) / 255.0
    y = fg * w + bg * (1 - w)

    return y.clip(0, 255).astype(np.uint8)


def dilate_box(region, radius):
    out = np.asarray(region, dtype=bool)
    radius = int(radius)
    if radius <= 0:
        return out.copy()
    for axis in (0, 1):
        moved = np.moveaxis(out, axis, 0)
        padded = np.pad(moved.astype(np.int32), [(radius, radius)] + [(0, 0)] * (moved.ndim - 1))
        total = np.concatenate(
            [np.zeros((1,) + padded.shape[1:], dtype=np.int32), np.cumsum(padded, axis=0, dtype=np.int32)]
        )
        width = 2 * radius + 1
        out = np.moveaxis((total[width:] - total[:-width]) > 0, 0, axis)
    return out


def feather_alpha(region, feather_px):
    region = np.asarray(region, dtype=bool)
    feather_px = int(feather_px)
    if feather_px <= 0:
        return region.astype(np.float32)
    core = dilate_box(region, feather_px // 2)
    soft = Image.fromarray(core.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(radius=feather_px / 6.0))
    alpha = np.asarray(soft, dtype=np.float32) / 255.0
    alpha[region] = 1.0
    alpha[~dilate_box(region, feather_px)] = 0.0
    return alpha


def match_ring_statistics(content, original, region, inner, outer):
    ring = dilate_box(region, outer) & ~dilate_box(region, inner)
    if int(ring.sum()) < 64:
        return content
    src = content[ring]
    ref = original[ring]
    mean_src = src.mean(axis=0)
    mean_ref = ref.mean(axis=0)
    gain = np.clip(ref.std(axis=0) / np.maximum(src.std(axis=0), 1e-3), 0.8, 1.25)
    return (content - mean_src) * gain + mean_ref


def composite_region(original, content, mask, feather, color_match):
    region = np.asarray(mask) >= 128
    if not region.any():
        return original.copy()
    ys, xs = np.nonzero(region)
    size = max(int(ys.max() - ys.min()) + 1, int(xs.max() - xs.min()) + 1)
    feather_px = max(2, int(round(float(feather) * size)))
    base, fresh, alpha = region_layers(original, content, region, feather_px, color_match)
    blended = base + (fresh - base) * alpha[:, :, None]
    return np.clip(np.rint(blended), 0, 255).astype(np.uint8)


def region_layers(original, content, region, feather_px, color_match):
    alpha = feather_alpha(region, feather_px)
    base = original.astype(np.float32)
    fresh = content.astype(np.float32)
    if color_match:
        fresh = match_ring_statistics(fresh, base, region, feather_px, feather_px + max(8, 2 * feather_px))
    return base, fresh, alpha


def region_patch(original, content, mask, feather_px, color_match):
    region = np.asarray(mask) >= 128
    height, width = region.shape[:2]
    if not region.any():
        rgb = np.clip(np.rint(content.astype(np.float32)), 0, 255).astype(np.uint8)
        alpha = np.full((height, width), 255, dtype=np.uint8)
        return np.dstack([rgb, alpha])
    _, fresh, alpha = region_layers(original, content, region, max(2, int(feather_px)), color_match)
    rgb = np.clip(np.rint(fresh), 0, 255).astype(np.uint8)
    return np.dstack([rgb, np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)])


def mask_patch(content, mask, feather_px):
    region = np.asarray(mask) >= 128
    height, width = region.shape[:2]
    rgb = np.clip(np.rint(content.astype(np.float32)), 0, 255).astype(np.uint8)
    if not region.any():
        return np.dstack([rgb, np.full((height, width), 255, dtype=np.uint8)])
    alpha = feather_alpha(region, max(2, int(feather_px)))
    return np.dstack([rgb, np.clip(np.rint(alpha * 255.0), 0, 255).astype(np.uint8)])


def alpha_over(original, patch):
    layer = patch.astype(np.float32)
    weight = layer[:, :, 3:4] / 255.0
    base = original.astype(np.float32)
    blended = base + (layer[:, :, :3] - base) * weight
    return np.clip(np.rint(blended), 0, 255).astype(np.uint8)


def outward_alpha(region, feather_px):
    import cv2

    region = np.asarray(region, dtype=bool)
    outside = cv2.distanceTransform((~region).astype(np.uint8), cv2.DIST_L2, 3)
    t = np.clip(outside / max(float(feather_px), 1.0), 0.0, 1.0)
    return np.where(region, 1.0, 1.0 - t * t * (3.0 - 2.0 * t)).astype(np.float32)


def region_size(region):
    ys, xs = np.nonzero(region)
    return max(int(ys.max() - ys.min()) + 1, int(xs.max() - xs.min()) + 1)


def composite_inpaint(original, content, mask, feather, feather_px=0):
    region = np.asarray(mask) >= 128
    if not region.any():
        return original.copy()
    feather_px = float(feather_px) if feather_px and feather_px > 0 else max(4.0, float(feather) * region_size(region))
    alpha = outward_alpha(region, feather_px)[:, :, None]
    base = original.astype(np.float32)
    blended = base + (content.astype(np.float32) - base) * alpha
    return np.clip(np.rint(blended), 0, 255).astype(np.uint8)


def _high_pass_gain(base, fresh, band, sigma):
    import cv2

    if band.sum() < 16:
        return np.ones(3, dtype=np.float32)
    pick = band > 0
    src_high = base - cv2.GaussianBlur(base, (0, 0), sigma)
    gen_high = fresh - cv2.GaussianBlur(fresh, (0, 0), sigma)
    ratio = src_high[pick].std(axis=0) / np.maximum(gen_high[pick].std(axis=0), 1e-3)
    return np.clip(ratio, 0.25, 1.0).astype(np.float32)


def composite_outpaint(original, content, mask, feather_px):
    import cv2

    region = np.asarray(mask) >= 128
    if not region.any():
        return original.copy()
    feather_px = max(4.0, float(feather_px))
    base = original.astype(np.float32)
    fresh = content.astype(np.float32)
    source = ~region
    inside = cv2.distanceTransform(source.astype(np.uint8), cv2.DIST_L2, 3)
    outside = cv2.distanceTransform(region.astype(np.uint8), cv2.DIST_L2, 3)
    band = (source & (inside <= max(2.0, feather_px / 4.0))).astype(np.float32)
    reach = np.clip(1.0 - outside / (3.0 * feather_px), 0.0, 1.0)[:, :, None]
    if band.sum() >= 16:
        sigma = max(2.0, feather_px)
        weighted = cv2.GaussianBlur((base - fresh) * band[:, :, None], (0, 0), sigma)
        weight = cv2.GaussianBlur(band, (0, 0), sigma)[:, :, None]
        fresh = fresh + np.where(weight > 1e-4, weighted / np.maximum(weight, 1e-4), 0.0) * reach
        detail_sigma = max(1.5, feather_px / 8.0)
        gain = _high_pass_gain(base, fresh, band, detail_sigma)
        low = cv2.GaussianBlur(fresh, (0, 0), detail_sigma)
        fresh = low + (fresh - low) * (1.0 + (gain - 1.0) * reach)
    t = np.clip(outside / feather_px, 0.0, 1.0)
    take = (t * t * (3.0 - 2.0 * t))[:, :, None]
    extension = base + (fresh - base) * take
    out = np.where(source[:, :, None], base, extension)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)
