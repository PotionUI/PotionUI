import os
from typing import Dict, Any, List, Tuple, Literal
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from ultralytics import YOLO
import logging

logger = logging.getLogger(__name__)


class DetailerHelper:
    """
    Helper class for the ADetailerPipe containing utility methods for:
    - Object detection (faces, hands)
    - Mask creation and manipulation
    - Region extraction and processing
    - Visualization
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    # -----------------------------
    #     DETECTOR METHODS
    # -----------------------------

    def load_detector(self, model_path: str) -> YOLO:
        """
        Load a YOLO detector model from the given path.

        Args:
            model_path: Full path to the YOLO model file (e.g., "models/detection_bbox/face_yolov12m.pt")
        """
        detector = YOLO(model_path)
        return detector.to(self.config.get("device", "cuda"))

    def detect_objects(self, image: Image.Image, detector: YOLO, dtype: Literal["face", "hand", "person"], classes: List[int] = None) -> List[np.ndarray]:
        """
        Use YOLO to find bounding boxes, each returned as [x1, y1, x2, y2].

        Args:
            image: Input image
            detector: YOLO model instance
            dtype: Type of detection (face, hand, person)
            classes: Optional list of class IDs to filter (e.g., [0] for person in COCO)
        """
        predict_kwargs = {
            "source": np.array(image),
            "conf": float(self.config["detections"][dtype]["confidence"]),
            "imgsz": max(image.size),
            "device": self.config["detections"][dtype].get("device", "cuda"),
            "verbose": True,
        }

        # Add class filtering if specified
        if classes is not None:
            predict_kwargs["classes"] = classes

        results = detector.predict(**predict_kwargs)

        boxes = []
        for result in results:
            for box in result.boxes.xyxy:
                x1, y1, x2, y2 = box.tolist()
                boxes.append(np.array([x1, y1, x2, y2]))
        return boxes

    def detect_mediapipe(self, image: Image.Image, model_path: str = None) -> List[np.ndarray]:
        """
        Use MediaPipe Tasks API for face detection using FaceLandmarker.

        Args:
            image: Input image
            model_path: Path to face_landmarker.task model file
        """
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        # Get model path from config if not provided
        if model_path is None:
            model_path = self.config["detections"]["face"].get("model", "models/mediapipe/face_landmarker.task")

        # Create base options with model path
        base_options = python.BaseOptions(model_asset_path=model_path)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.IMAGE,
            num_faces=10,
            min_face_detection_confidence=float(self.config["detections"]["face"]["confidence"])
        )

        # Create face landmarker
        with vision.FaceLandmarker.create_from_options(options) as landmarker:
            # Convert PIL Image to MediaPipe Image
            import mediapipe as mp
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.array(image))

            # Detect faces
            results = landmarker.detect(mp_image)
            if not results.face_landmarks:
                return []

            boxes = []
            w, h = image.width, image.height

            # Calculate bounding box from landmarks
            for face_landmarks in results.face_landmarks:
                # Get all landmark positions
                xs = [landmark.x * w for landmark in face_landmarks]
                ys = [landmark.y * h for landmark in face_landmarks]

                # Create bounding box
                x1 = min(xs)
                y1 = min(ys)
                x2 = max(xs)
                y2 = max(ys)

                boxes.append(np.array([x1, y1, x2, y2]))

            return boxes
    
    def detect_eyes_mediapipe(self, image: Image.Image, model_path: str = None) -> List[np.ndarray]:
        """
        Use MediaPipe Tasks API for eye detection using FaceLandmarker.
        Returns a single bounding box that encompasses both eyes for each face.

        Args:
            image: Input image
            model_path: Path to face_landmarker.task model file
        """
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        import mediapipe as mp

        # Eye landmark indices for face mesh (478 landmarks in new API)
        LEFT_EYE_INDICES = [33, 160, 158, 133, 153, 144, 163, 7]
        RIGHT_EYE_INDICES = [362, 385, 387, 263, 373, 380, 374, 249]

        # Get model path from config if not provided
        if model_path is None:
            model_path = self.config["detections"].get("eyes", {}).get("model", "models/mediapipe/face_landmarker.task")

        # Create base options with model path
        base_options = python.BaseOptions(model_asset_path=model_path)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.IMAGE,
            num_faces=10,
            min_face_detection_confidence=float(self.config["detections"].get("eyes", {}).get("confidence", 0.5))
        )

        # Create face landmarker
        with vision.FaceLandmarker.create_from_options(options) as landmarker:
            # Convert PIL Image to MediaPipe Image
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.array(image))

            # Detect face landmarks
            results = landmarker.detect(mp_image)
            if not results.face_landmarks:
                return []

            boxes = []
            w, h = image.width, image.height

            for face_landmarks in results.face_landmarks:
                all_eye_points = []

                # Collect all points from both eyes
                for idx in LEFT_EYE_INDICES + RIGHT_EYE_INDICES:
                    if idx < len(face_landmarks):
                        landmark = face_landmarks[idx]
                        all_eye_points.append((landmark.x * w, landmark.y * h))

                if all_eye_points:
                    # Create a single bounding box that encompasses both eyes
                    xs = [p[0] for p in all_eye_points]
                    ys = [p[1] for p in all_eye_points]

                    # Use different padding for horizontal (between eyes) and vertical
                    h_padding = int(self.config["detections"].get("eyes", {}).get("eye_padding", 10))
                    v_padding = int(self.config["detections"].get("eyes", {}).get("eye_v_padding", h_padding * 1.5))

                    # Calculate the box to include both eyes with appropriate padding
                    boxes.append(np.array([
                        min(xs) - h_padding,
                        min(ys) - v_padding,
                        max(xs) + h_padding,
                        max(ys) + v_padding
                    ]))

            return boxes

    def detect_teeth_mediapipe(self, image: Image.Image, model_path: str = None) -> List[np.ndarray]:
        """
        Use MediaPipe Tasks API for teeth detection using FaceLandmarker.
        Returns bounding boxes for visible teeth regions (only when mouth is open).

        Args:
            image: Input image
            model_path: Path to face_landmarker.task model file
        """
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        import mediapipe as mp

        # Mouth landmark indices for face mesh (478 landmarks in new API)
        # Outer lip landmarks - creates a more complete mouth region
        UPPER_OUTER_LIP = [61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291]
        LOWER_OUTER_LIP = [146, 91, 181, 84, 17, 314, 405, 321, 375, 291]
        # Inner lip landmarks - for teeth area
        UPPER_INNER_LIP = [78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308]
        LOWER_INNER_LIP = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308]

        # Get model path from config if not provided
        if model_path is None:
            model_path = self.config["detections"].get("teeth", {}).get("model", "models/mediapipe/face_landmarker.task")

        # Create base options with model path
        base_options = python.BaseOptions(model_asset_path=model_path)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.IMAGE,
            num_faces=10,
            min_face_detection_confidence=float(self.config["detections"].get("teeth", {}).get("confidence", 0.5))
        )

        # Create face landmarker
        with vision.FaceLandmarker.create_from_options(options) as landmarker:
            # Convert PIL Image to MediaPipe Image
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.array(image))

            # Detect face landmarks
            results = landmarker.detect(mp_image)
            if not results.face_landmarks:
                return []

            boxes = []
            w, h = image.width, image.height
            mouth_threshold = float(self.config["detections"].get("teeth", {}).get("mouth_open_threshold", 0.015))

            for face_landmarks in results.face_landmarks:
                # Calculate if mouth is open by measuring vertical distance between upper and lower lip centers
                if len(face_landmarks) > 14:
                    upper_lip_center = face_landmarks[13]  # Upper lip center
                    lower_lip_center = face_landmarks[14]  # Lower lip center

                    mouth_openness = abs(upper_lip_center.y - lower_lip_center.y)

                    # Only process if mouth is open enough to show teeth
                    if mouth_openness < mouth_threshold:
                        continue

                    # Collect points from inner lip landmarks for precise teeth area
                    teeth_points = []
                    for idx in UPPER_INNER_LIP + LOWER_INNER_LIP:
                        if idx < len(face_landmarks):
                            landmark = face_landmarks[idx]
                            teeth_points.append((landmark.x * w, landmark.y * h))

                    if teeth_points:
                        # Create bounding box around teeth area
                        xs = [p[0] for p in teeth_points]
                        ys = [p[1] for p in teeth_points]

                        # Use configurable padding
                        h_padding = int(self.config["detections"].get("teeth", {}).get("teeth_padding", 5))
                        v_padding = int(self.config["detections"].get("teeth", {}).get("teeth_v_padding", 8))

                        boxes.append(np.array([
                            min(xs) - h_padding,
                            min(ys) - v_padding,
                            max(xs) + h_padding,
                            max(ys) + v_padding
                        ]))

            return boxes

    # -----------------------------
    #     UTILITY METHODS
    # -----------------------------

    def calculate_box_size(self, image_size, box: np.ndarray, pad: int) -> List[int]:
        """
        Expand the bounding box by 'pad' pixels on each side.
        """
        x1, y1, x2, y2 = box.astype(int)
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(image_size[0], x2 + pad)
        y2 = min(image_size[1], y2 + pad)
        return [x1, y1, x2, y2]

    def round_to_closest_multiple(self, number: int, multiple: int = 8) -> int:
        """
        Round a number to the nearest multiple of 'multiple' (8 by default).
        """
        return multiple * round(number / multiple)

    def visualize_detections(self, image: Image.Image, boxes: List[np.ndarray], dtype: str) -> Image.Image:
        """
        Draw bounding boxes for a single detection type (face or hand).
        """
        vis_image = image.copy()
        draw = ImageDraw.Draw(vis_image)

        pad = int(self.config["detections"][dtype]["padding"])
        # PIL rejects a list outright ("color must be int or tuple"), and this
        # value arrives as a list from anything that came through YAML or JSON.
        # Same normalization BaseDetector.get_visualization_color() applies.
        color = tuple(self.config["detections"][dtype]["box_color"])
        thickness = int(self.config["detections"][dtype]["box_thickness"])

        for box in boxes:
            x1, y1, x2, y2 = self.calculate_box_size(image.size, box, pad)
            draw.rectangle([(x1, y1), (x2, y2)], outline=color, width=thickness)
            draw.text((x1, y1 - 10), dtype, fill=color)

        return vis_image

    def filter_boxes_by_ratio(
            self,
            boxes: List[np.ndarray],
            image_size: Tuple[int, int],
            dtype: Literal["face", "hand"]
    ) -> List[np.ndarray]:
        """
        Filter out bounding boxes that are too small or too large
        based on mask_min_ratio/mask_max_ratio from the config.
        """
        w, h = image_size
        min_area = float(self.config["detections"][dtype]["mask_min_ratio"]) * w * h
        max_area = float(self.config["detections"][dtype]["mask_max_ratio"]) * w * h
        filtered = []

        for box in boxes:
            x1, y1, x2, y2 = box
            box_area = (x2 - x1) * (y2 - y1)
            if min_area <= box_area <= max_area:
                filtered.append(box)

        return filtered

    def adjust_steps_based_on_face_size(self, face_box: np.ndarray, image_size: tuple) -> int:
        """
        Example logic: if the face is large in the overall image,
        reduce the steps to speed up or avoid overprocessing.
        """
        face_area = (face_box[2] - face_box[0]) * (face_box[3] - face_box[1])
        image_area = image_size[0] * image_size[1]
        ratio = face_area / image_area

        face_cfg = self.config["detections"]["face"]
        if ratio > float(face_cfg["size_threshold"]):
            reduction_factor = 1 - float(face_cfg["step_reduction_percentage"])
            adjusted_steps = int(int(face_cfg["steps"]) * reduction_factor)
            return max(1, adjusted_steps)
        return int(face_cfg["steps"])

    # -----------------------------
    #     MASK-RELATED METHODS
    # -----------------------------

    def create_feathered_mask(self, size: tuple, box: List[int], feather_ratio: float = 0.2) -> Image.Image:
        """
        Create a mask with feathered edges for smoother blending
        """
        mask = Image.new('L', size, 0)
        draw = ImageDraw.Draw(mask)

        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1

        # Calculate feather width
        feather_x = int(width * feather_ratio)
        feather_y = int(height * feather_ratio)

        # Draw the core area with full opacity
        draw.rectangle([x1 + feather_x, y1 + feather_y, x2 - feather_x, y2 - feather_y], fill=255)

        # Create gradient for edges
        for i in range(feather_x):
            opacity = int(255 * (i / feather_x))
            # Left edge
            draw.rectangle([x1 + i, y1 + feather_y, x1 + i + 1, y2 - feather_y], fill=opacity)
            # Right edge
            draw.rectangle([x2 - i - 1, y1 + feather_y, x2 - i, y2 - feather_y], fill=opacity)
        for i in range(feather_y):
            opacity = int(255 * (i / feather_y))
            # Top edge
            draw.rectangle([x1 + feather_x, y1 + i, x2 - feather_x, y1 + i + 1], fill=opacity)
            # Bottom edge
            draw.rectangle([x1 + feather_x, y2 - i - 1, x2 - feather_x, y2 - i], fill=opacity)

        return mask

    def create_eye_region_mask(self, size: tuple, box: List[int]) -> Image.Image:
        """
        Create a specialized mask for the eye region that provides smooth blending
        while maintaining focus on both eyes equally.
        """
        mask = Image.new('L', size, 0)
        draw = ImageDraw.Draw(mask)
        
        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1
        
        # Create an elliptical mask shape for more natural eye region blending
        # This helps avoid harsh rectangular edges around the eye area
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2
        
        # Create gradient ellipse
        for i in range(min(width, height) // 2, 0, -1):
            opacity = int(255 * (1 - (i / (min(width, height) / 2)) ** 2))
            draw.ellipse(
                [center_x - width//2 + i, center_y - height//2 + i,
                 center_x + width//2 - i, center_y + height//2 - i],
                fill=opacity
            )

        # Anti-alias the discrete ellipse steps
        mask = mask.filter(ImageFilter.GaussianBlur(radius=1))
        return mask

    def create_adaptive_mask(self, size: tuple, box: List[int], feather_ratio: float = 0.15) -> Image.Image:
        """
        Create a mask with higher central focus and a sharper falloff at edges
        """
        mask = Image.new('L', size, 0)
        draw = ImageDraw.Draw(mask)

        x1, y1, x2, y2 = box
        width = x2 - x1
        height = y2 - y1

        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2

        for y in range(y1, y2):
            for x in range(x1, x2):
                dx = (x - center_x) / (width / 2)
                dy = (y - center_y) / (height / 2)
                distance = min(1.0, (dx * dx + dy * dy) ** 0.5)

                # Sharper falloff
                if distance < 0.5:
                    opacity = 255
                else:
                    opacity = int(255 * (1 - ((distance - 0.5) * 2)))
                draw.point((x, y), fill=opacity)

        return mask

    # -----------------------------
    #     REGION PROCESSING METHODS
    # -----------------------------

    def match_color_statistics(self, source_img: Image.Image, target_img: Image.Image) -> Image.Image:
        """
        Simplified color matching that preserves local contrast and reduces artifacts
        """
        source = np.array(source_img).astype(np.float32)
        target = np.array(target_img).astype(np.float32)

        # Calculate mean and std for each channel
        for i in range(3):
            s_mean = np.mean(source[:, :, i])
            t_mean = np.mean(target[:, :, i])
            s_std = np.std(source[:, :, i])
            t_std = np.std(target[:, :, i])

            if t_std > 0:  # Prevent division by zero
                target[:, :, i] = (target[:, :, i] - t_mean) * (s_std / t_std) + t_mean

        target = np.clip(target, 0, 255).astype(np.uint8)
        return Image.fromarray(target)

    def extract_regions(
            self,
            image: Image.Image,
            boxes: List[np.ndarray],
            dtype: Literal["face", "hand"]
    ) -> List[Tuple[Image.Image, Image.Image, tuple]]:
        """
        Extract bounding boxes with padding, then create a feathered mask for each region.
        """
        regions = []
        for box in boxes:
            x1, y1, x2, y2 = box.astype(int)
            # Expand bounding box
            pad = self.config["detections"][dtype]["padding"]
            context_pad = int(pad * 1.5)  # more context helps face/hand details

            px1 = max(0, x1 - context_pad)
            py1 = max(0, y1 - context_pad)
            px2 = min(image.size[0], x2 + context_pad)
            py2 = min(image.size[1], y2 + context_pad)

            width = self.round_to_closest_multiple(px2 - px1)
            height = self.round_to_closest_multiple(py2 - py1)

            # Crop & resize region
            region = image.crop((px1, py1, px2, py2))
            region = region.resize((width, height), Image.LANCZOS)

            # Create mask (feathered rectangle)
            mask_coords = [context_pad, context_pad, width - context_pad, height - context_pad]
            mask = self.create_feathered_mask((width, height), mask_coords, feather_ratio=0.2)

            # Optional extra blur
            if int(self.config["detections"][dtype]["mask_blur"]) > 0:
                mask = mask.filter(ImageFilter.GaussianBlur(
                    radius=int(self.config["detections"][dtype]["mask_blur"]))
                )

            regions.append((region, mask, (px1, py1, px2, py2, width, height)))

        return regions

    def paste_region(
            self,
            original: Image.Image,
            enhanced_region: Image.Image,
            region_mask: Image.Image,
            coords: tuple
    ) -> Image.Image:
        """
        Paste the enhanced region with simplified color matching and the given mask.
        """
        result = original.copy()
        x1, y1, x2, y2, orig_width, orig_height = coords

        # Resize to match original
        enhanced_region = enhanced_region.resize((x2 - x1, y2 - y1), Image.LANCZOS)
        region_mask = region_mask.resize((x2 - x1, y2 - y1), Image.LANCZOS)

        # Optional color matching
        original_region = original.crop((x1, y1, x2, y2))
        enhanced_region = self.match_color_statistics(original_region, enhanced_region)

        # Paste
        result.paste(enhanced_region, (x1, y1), region_mask)
        return result

    # -----------------------------
    #     UPSCALING METHODS
    # -----------------------------

    def get_pil_resampling_filter(self, method: str):
        """Get PIL resampling filter from method name."""
        filters = {
            "NEAREST": Image.NEAREST,
            "BILINEAR": Image.BILINEAR,
            "BICUBIC": Image.BICUBIC,
            "LANCZOS": Image.LANCZOS,
        }
        return filters.get(method, Image.LANCZOS)

    def should_upscale_region(self, region_size: Tuple[int, int], dtype: str) -> bool:
        """Determine if a region should be upscaled based on its size."""
        width, height = region_size
        min_size = int(self.config["detections"][dtype].get("min_size_for_upscale", 128))
        enable_upscale = self.config["detections"][dtype].get("enable_upscale", True)

        if not enable_upscale:
            return False

        return min(width, height) < min_size * 2  # Upscale if smaller than 2x minimum

    def upscale_region(self, region: Image.Image, mask: Image.Image, dtype: str) -> Tuple[Image.Image, Image.Image, Tuple[int, int]]:
        """Upscale a region and its mask by the configured factor."""
        factor = float(self.config["detections"][dtype].get("upscale_factor", 2.0))
        method = self.config["detections"][dtype].get("upscale_method", "LANCZOS")

        original_size = region.size
        new_width = int(region.width * factor)
        new_height = int(region.height * factor)

        # Round to multiple of 8 for better compatibility
        new_width = self.round_to_closest_multiple(new_width)
        new_height = self.round_to_closest_multiple(new_height)

        resampling_filter = self.get_pil_resampling_filter(method)
        upscaled_region = region.resize((new_width, new_height), resampling_filter)
        upscaled_mask = mask.resize((new_width, new_height), resampling_filter)

        logger.debug(f"[DETAILER] Upscaled {dtype} from {original_size} to {upscaled_region.size} using {method}")
        return upscaled_region, upscaled_mask, original_size

    def downscale_region(self, region: Image.Image, target_size: Tuple[int, int], method: str = "LANCZOS") -> Image.Image:
        """Downscale a region back to the target size."""
        resampling_filter = self.get_pil_resampling_filter(method)
        downscaled = region.resize(target_size, resampling_filter)

        logger.debug(f"[DETAILER] Downscaled region from {region.size} to {target_size}")
        return downscaled
