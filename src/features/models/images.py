import logging
from typing import Optional, List, Dict, Any
from pathlib import Path
import uuid
from PIL import Image

logger = logging.getLogger(__name__)

class ModelImageService:
    """Service for downloading and storing model images and videos offline"""

    def __init__(self, storage_dir: Optional[str] = None):
        # Use provided storage_dir or get from settings
        if storage_dir:
            self.storage_dir = Path(storage_dir)
        else:
            # Try to get from settings, but handle case where dependency injection isn't available
            try:
                from src.platform.settings.settings import Settings
                from src.platform.settings.repository import SettingRepository

                # Create dependencies for Settings
                setting_repo = SettingRepository()
                settings = Settings(setting_repo)
                models_media_dir = settings.get_models_media_directory()
                if models_media_dir:
                    self.storage_dir = Path(models_media_dir)
                else:
                    # Fallback to default if settings not available
                    self.storage_dir = Path("storage/models")
            except Exception as e:
                # If dependency injection fails, use default
                logger.warning(f"Failed to get models media directory from settings, using default: {e}")
                self.storage_dir = Path("storage/models")

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_files_per_model = 10  # Limit number of files to download (images + videos)
    
    def get_model_media_dir(self, model_id: str) -> Path:
        """Get the directory for storing a specific model's media files"""
        model_dir = self.storage_dir / model_id
        model_dir.mkdir(exist_ok=True)
        return model_dir

# Global service instance
model_image_service = ModelImageService()


async def generate_missing_thumbnails_from_videos(model_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Standalone function to generate thumbnails from videos for models that don't have any images.
    Can be called independently of any provider service.
    """
    from datetime import datetime
    from src.features.models.repository import model_repo
    from src.features.generation.file_repository import FileRepository
    from .video_thumbnail import video_thumbnail_service

    logger.info("Starting thumbnail generation from videos for models without images")
    start_time = datetime.now()

    # Get models to process
    if model_ids:
        models = [model_repo.get_by_id(mid) for mid in model_ids]
        models = [m for m in models if m]  # Filter out None values
    else:
        # Get all models
        models = model_repo.get_all(limit=None)

    successful = 0
    failed = 0
    skipped = 0

    file_repo = FileRepository()

    for model in models:
        try:
            # Get all files for this model
            model_files = model_repo.get_model_files(model.id)

            if not model_files:
                logger.debug(f"Model {model.id} has no associated files, skipping")
                skipped += 1
                continue

            # Check file types
            image_files = []
            video_files = []

            for model_file in model_files:
                file_record = file_repo.get_by_id(model_file.file_id)
                if file_record:
                    if file_record.file_type in ['IMAGE'] or model_file.file_type in ['image', 'thumbnail']:
                        image_files.append(file_record)
                    elif file_record.file_type in ['VIDEO'] or model_file.file_type in ['video']:
                        video_files.append(file_record)

            # Skip if already has images
            if image_files:
                logger.debug(f"Model {model.id} already has {len(image_files)} images, skipping")
                skipped += 1
                continue

            # Skip if no videos
            if not video_files:
                logger.debug(f"Model {model.id} has no videos to generate thumbnail from, skipping")
                skipped += 1
                continue

            logger.debug(f"Generating thumbnail for model {model.id} ({model.filename}) from {len(video_files)} videos")

            # Try to generate thumbnail from the first video
            thumbnail_generated = False
            for video_file in video_files:
                try:
                    video_path = video_file.file_path
                    if not Path(video_path).exists():
                        logger.warning(f"Video file not found: {video_path}")
                        continue

                    # Extract frame from video
                    frame = video_thumbnail_service.extract_random_frame(video_path)
                    if frame is None:
                        continue

                    # Convert to PIL Image
                    pil_image = video_thumbnail_service.frame_to_pil_image(frame)
                    if pil_image is None:
                        continue

                    # Save as thumbnail
                    model_dir = model_image_service.get_model_media_dir(model.id)
                    thumbnail_path = model_dir / f"thumbnail_from_video_{uuid.uuid4().hex[:8]}.jpg"

                    # Resize to reasonable size
                    max_size = (1024, 1024)
                    pil_image.thumbnail(max_size, Image.Resampling.LANCZOS)
                    pil_image.save(thumbnail_path, "JPEG", quality=85)

                    # Create file record for the thumbnail
                    from src.features.generation.records import File
                    from src.features.models.records import ModelFile

                    file_record = File(
                        filename=thumbnail_path.name,
                        file_path=str(thumbnail_path),
                        file_type='IMAGE',
                        generation_id=None
                    )
                    file_repo.create(file_record)

                    # Link to model
                    model_file = ModelFile(
                        model_id=model.id,
                        file_id=file_record.id,
                        file_type='thumbnail',
                        source='video_extraction'
                    )
                    model_repo.add_model_file(model_file)

                    thumbnail_generated = True
                    logger.debug(f"Generated thumbnail for model {model.id} from video")
                    break

                except Exception as e:
                    logger.error(f"Error extracting thumbnail from video {video_file.id}: {e}")
                    continue

            if thumbnail_generated:
                successful += 1
            else:
                failed += 1

        except Exception as e:
            logger.error(f"Error generating thumbnail for model {model.id}: {e}")
            failed += 1

    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    result_summary = {
        'processed': len(models),
        'successful': successful,
        'failed': failed,
        'skipped': skipped,
        'total': len(models),
        'duration': duration
    }

    logger.info(f"Thumbnail generation completed: {result_summary}")
    return result_summary