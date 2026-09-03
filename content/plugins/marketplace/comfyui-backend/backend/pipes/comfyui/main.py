import asyncio
import json
import uuid
import io
import os
import base64
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image
import websockets
import aiohttp

from src.plugin_api import (
    BasePipe,
    ComfyUIWorkflowGenerationOutput,
    GenerationExecutionError,
    Icon,
    ImageGenerationOutput,
    IOType,
    PipeConfigSpec,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    Progress,
    ProgressGenerationOutput,
    VideoGenerationOutput,
    logger,
)


class ComfyUIPipe(BasePipe):
    name = "comfyui"
    description = "Execute ComfyUI workflows with dynamic field mapping"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.ws = None
        self.client_id = None
        self.current_node = None
        self.total_nodes = 0
        self.executed_nodes = 0
        self.workflow_nodes = {}  # Store workflow node information

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "host": "127.0.0.1",
            "port": 8188,
            "workflow_file": "",
            "field_mappings": [],
            "node_manipulations": [],
            "timeout": 300,
            "client_id": None,
            "secure": False,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec(
                name="host",
                param_type=str,
                default="127.0.0.1",
                description="ComfyUI server host",
                required=False
            ),
            PipeConfigSpec(
                name="port",
                param_type=int,
                default=8188,
                description="ComfyUI server port",
                required=False,
                min_value=1,
                max_value=65535
            ),
            PipeConfigSpec(
                name="workflow_file",
                param_type=str,
                default="",
                description="Path to ComfyUI workflow JSON file",
                required=True
            ),
            PipeConfigSpec(
                name="field_mappings",
                param_type=list,
                default=[],
                description="List of field mappings [source, target, type]",
                required=True
            ),
            PipeConfigSpec(
                name="node_manipulations",
                param_type=list,
                default=[],
                description="List of node manipulations (remove, reroute, bypass)",
                required=False
            ),
            PipeConfigSpec(
                name="timeout",
                param_type=int,
                default=300,
                description="Connection timeout in seconds",
                required=False,
                min_value=1,
                max_value=3600
            ),
            PipeConfigSpec(
                name="client_id",
                param_type=str,
                default=None,
                description="Optional client ID for connection",
                required=False
            ),
            PipeConfigSpec(
                name="secure",
                param_type=bool,
                default=False,
                description="Use secure WebSocket connection (wss://)",
                required=False
            ),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("conditioning", IOType.CONDITIONING, False, "Prompt conditioning", is_array=True),
            PipeInputSpec("seed", IOType.SEED, False, "Random seeds", is_array=True),
            PipeInputSpec("image", IOType.IMAGE, False, "Input images for img2img", is_array=True),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("image", IOType.IMAGE, "Generated images from ComfyUI", is_array=True),
            PipeOutputSpec("video", IOType.VIDEO, "Generated videos from ComfyUI", is_array=True),
        ]

    def load_workflow_file(self) -> Dict[str, Any]:
        """Load the workflow JSON file"""
        workflow_path = Path(self.config["workflow_file"])
        if not workflow_path.exists():
            raise FileNotFoundError(f"Workflow file not found: {workflow_path}")

        with open(workflow_path, 'r') as f:
            return json.load(f)

    def build_node_name_mapping(self, workflow: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
        """Build a mapping from node names (_meta.title) to node IDs.

        Returns:
            Tuple of (name_to_id, duplicates):
            - name_to_id: Dict mapping unique node names to their IDs
            - duplicates: Dict mapping duplicate names to list of all their IDs
        """
        name_to_id = {}
        duplicates = {}

        for node_id, node_data in workflow.items():
            title = node_data.get('_meta', {}).get('title')
            if title:
                if title in name_to_id:
                    # Track duplicates
                    if title not in duplicates:
                        duplicates[title] = [name_to_id[title]]
                    duplicates[title].append(node_id)
                else:
                    name_to_id[title] = node_id

        return name_to_id, duplicates

    def resolve_node_reference(self, ref: str, name_to_id: Dict[str, str],
                               duplicates: Dict[str, List[str]]) -> str:
        """Resolve a node reference (name or ID) to a node ID.

        If ref is a node name, look it up in name_to_id.
        If ref is already a node ID (not in name_to_id), return it unchanged.
        Raise ValueError if the name has duplicates.

        Args:
            ref: Node reference (can be name or ID)
            name_to_id: Mapping from unique node names to IDs
            duplicates: Mapping from duplicate names to all their IDs

        Returns:
            The resolved node ID

        Raises:
            ValueError: If the reference is a duplicate node name
        """
        # Check if it's a duplicate name being used
        if ref in duplicates:
            raise ValueError(
                f"Cannot use node name '{ref}' - it appears in multiple nodes: "
                f"{', '.join(duplicates[ref])}. Use node ID instead."
            )

        # Check if it's a known name
        if ref in name_to_id:
            resolved_id = name_to_id[ref]
            logger.debug(f"Resolved node name '{ref}' to ID '{resolved_id}'")
            return resolved_id

        # Assume it's already a node ID
        return ref

    def resolve_connection_references(self, node_config: Dict[str, Any],
                                      name_to_id: Dict[str, str],
                                      duplicates: Dict[str, List[str]]) -> Dict[str, Any]:
        """Resolve node name references in connection arrays within a node config.

        Connection arrays are in the format [node_ref, output_index] where
        node_ref can be a node name or ID.

        Args:
            node_config: Node configuration dict with 'inputs' field
            name_to_id: Mapping from unique node names to IDs
            duplicates: Mapping from duplicate names to all their IDs

        Returns:
            A deep copy of node_config with all connection references resolved
        """
        if 'inputs' not in node_config:
            return node_config

        resolved_config = deepcopy(node_config)
        for key, value in resolved_config['inputs'].items():
            # Check if it's a connection array [node_ref, output_index]
            if isinstance(value, list) and len(value) == 2:
                if isinstance(value[0], str) and isinstance(value[1], int):
                    resolved_id = self.resolve_node_reference(value[0], name_to_id, duplicates)
                    resolved_config['inputs'][key] = [resolved_id, value[1]]

        return resolved_config

    def set_nested_value(self, obj: Dict[str, Any], path: str, value: Any,
                         name_to_id: Dict[str, str] = None,
                         duplicates: Dict[str, List[str]] = None):
        """Set value in nested dict using dot notation like '3.inputs.seed'.

        The first path component (node reference) can be a node name or ID
        when name_to_id mapping is provided.

        Args:
            obj: The workflow dictionary to modify
            path: Dot-notation path like 'KSampler.inputs.seed' or '3.inputs.seed'
            value: The value to set
            name_to_id: Optional mapping from node names to IDs for resolution
            duplicates: Optional mapping of duplicate names to their IDs
        """
        keys = path.split('.')

        # Resolve the first key (node reference) if mappings provided
        if name_to_id is not None and keys:
            keys[0] = self.resolve_node_reference(keys[0], name_to_id, duplicates or {})

        current = obj

        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

    def cast_value(self, value: Any, type_name: str) -> Any:
        """Cast value to specified type"""
        if value is None:
            return None

        try:
            if type_name == "int":
                return int(float(value))
            elif type_name == "float":
                return float(value)
            elif type_name == "bool":
                if isinstance(value, str):
                    return value.lower() in ("true", "1", "yes", "on")
                return bool(value)
            elif type_name == "str":
                return str(value)
            else:
                return value
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to cast {value} to {type_name}: {e}")
            return value

    def extract_workflow_info(self, workflow: Dict[str, Any]):
        """Extract node information from workflow for descriptive progress messages"""
        self.workflow_nodes = {}
        for node_id, node_data in workflow.items():
            node_info = {
                'id': node_id,
                'class_type': node_data.get('class_type', 'Unknown'),
                'title': node_data.get('_meta', {}).get('title', node_data.get('class_type', 'Unknown')),
                'inputs': node_data.get('inputs', {})
            }
            self.workflow_nodes[node_id] = node_info

    def get_descriptive_node_message(self, node_id: str, progress: int, total: int) -> str:
        """Create a descriptive message for node execution with templating"""
        if not node_id or node_id not in self.workflow_nodes:
            return f"Executing node {node_id} ({progress}/{total})"

        node_info = self.workflow_nodes[node_id]
        class_type = node_info['class_type']
        title = node_info['title']
        inputs = node_info['inputs']

        # Create rich templated message based on node type
        message_parts = []

        # Add pipe information
        pipe_icon = self.get_node_icon(class_type)
        message_parts.append(f"<<PIPE:{title}:{pipe_icon}>>")

        # Add progress information
        progress_percent = int((progress / total) * 100) if total > 0 else 0
        message_parts.append(f"<<PROGRESS:{progress_percent}%:chart>>")

        # Add specific information based on node type
        type_info = self.get_node_type_info(class_type, inputs)
        if type_info:
            message_parts.append(type_info)

        return " ".join(message_parts)

    def get_node_icon(self, class_type: str) -> str:
        """Get appropriate icon for node type"""
        icon_map = {
            'CheckpointLoaderSimple': 'document',
            'LoraLoader': 'document',
            'CLIPTextEncode': 'user',
            'KSampler': 'bolt',
            'KSamplerAdvanced': 'bolt',
            'VAEDecode': 'computer',
            'VAEEncode': 'computer',
            'SaveImage': 'photo',
            'LoadImage': 'photo',
            'EmptyLatentImage': 'photo',
            'UpscaleModelLoader': 'gear',
            'ImageUpscaleWithModel': 'gear',
        }
        return icon_map.get(class_type, 'cog')

    def get_node_type_info(self, class_type: str, inputs: Dict[str, Any]) -> str:
        """Get specific information about the node type"""
        if class_type == 'CheckpointLoaderSimple':
            model_name = self._extract_value(inputs.get('ckpt_name', ''))
            if model_name:
                model_name = str(model_name).replace('.safetensors', '').replace('.ckpt', '')
                return f"<<MODEL:{model_name}:document>>"

        elif class_type == 'LoraLoader':
            lora_name = self._extract_value(inputs.get('lora_name', ''))
            if lora_name:
                lora_name = str(lora_name).replace('.safetensors', '')
                return f"<<MODEL:{lora_name}:document>>"

        elif class_type in ['KSampler', 'KSamplerAdvanced']:
            steps = self._extract_value(inputs.get('steps', 0))
            sampler = self._extract_value(inputs.get('sampler_name', ''))

            # Only show steps and sampler if they are actual values (not node references)
            info_parts = []
            if steps is not None and not isinstance(steps, list):
                try:
                    steps_int = int(steps)
                    if steps_int > 0:
                        info_parts.append(f"<<NUMBER:{steps_int} steps:bolt>>")
                except (ValueError, TypeError):
                    pass
            if sampler and not isinstance(sampler, list) and isinstance(sampler, str):
                info_parts.append(f"<<EFFECT:{sampler}:gear>>")

            return " ".join(info_parts)

        elif class_type == 'EmptyLatentImage':
            width = self._extract_value(inputs.get('width', 0))
            height = self._extract_value(inputs.get('height', 0))

            # Only show resolution if both are actual values (not node references)
            if width is not None and height is not None and not isinstance(width, list) and not isinstance(height, list):
                try:
                    width_int = int(width)
                    height_int = int(height)
                    if width_int > 0 and height_int > 0:
                        return f"<<RESOLUTION:{width_int}x{height_int}:photo>>"
                except (ValueError, TypeError):
                    pass

        elif class_type == 'SaveImage':
            prefix = self._extract_value(inputs.get('filename_prefix', 'ComfyUI'))
            if prefix and not isinstance(prefix, list):
                return f"<<EFFECT:{prefix}:photo>>"

        return ""

    def _extract_value(self, value: Any) -> Any:
        """Extract actual value, handling node references"""
        if isinstance(value, list) and len(value) == 2:
            # This is likely a node reference [node_id, output_index]
            # We can't resolve it here, so return None
            return None
        return value

    async def upload_image_to_comfyui(self, image: Image.Image, generation_outputs: callable) -> Optional[str]:
        """Upload image to ComfyUI and return the filename"""
        try:
            # Convert PIL image to bytes
            img_buffer = io.BytesIO()
            image.save(img_buffer, format='PNG')
            img_bytes = img_buffer.getvalue()

            protocol = "https" if self.config.get("secure", False) else "http"
            url = f"{protocol}://{self.config['host']}:{self.config['port']}/upload/image"

            # Create form data for the upload
            data = aiohttp.FormData()
            data.add_field('image', img_bytes, filename='input_image.png', content_type='image/png')
            data.add_field('type', 'input')

            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        filename = result.get('name', 'input_image.png')
                        logger.info(f"Uploaded image to ComfyUI: {filename}")
                        return filename
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to upload image: {response.status} - {error_text}")
                        generation_outputs(ProgressGenerationOutput(
                            state=f"Failed to upload image: {response.status}",
                            icon=Icon("x-circle")
                        ))
                        return None

        except Exception as e:
            logger.error(f"Error uploading image to ComfyUI: {e}")
            generation_outputs(ProgressGenerationOutput(
                state=f"Error uploading image: {str(e)}",
                icon=Icon("x-circle")
            ))
            return None

    async def upload_video_to_comfyui(self, video_path: Path, generation_outputs: callable) -> Optional[str]:
        """Upload video file to ComfyUI input directory and return the filename"""
        try:
            video_bytes = video_path.read_bytes()
            suffix = video_path.suffix or '.mp4'
            content_type = {
                '.mp4': 'video/mp4',
                '.webm': 'video/webm',
                '.avi': 'video/x-msvideo',
                '.mov': 'video/quicktime',
                '.mkv': 'video/x-matroska',
            }.get(suffix.lower(), 'video/mp4')

            protocol = "https" if self.config.get("secure", False) else "http"
            url = f"{protocol}://{self.config['host']}:{self.config['port']}/upload/image"

            data = aiohttp.FormData()
            data.add_field('image', video_bytes, filename=video_path.name, content_type=content_type)
            data.add_field('type', 'input')

            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        filename = result.get('name', video_path.name)
                        logger.info(f"Uploaded video to ComfyUI: {filename}")
                        return filename
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to upload video: {response.status} - {error_text}")
                        generation_outputs(ProgressGenerationOutput(
                            state=f"Failed to upload video: {response.status}",
                            icon=Icon("x-circle")
                        ))
                        return None

        except Exception as e:
            logger.error(f"Error uploading video to ComfyUI: {e}")
            generation_outputs(ProgressGenerationOutput(
                state=f"Error uploading video: {str(e)}",
                icon=Icon("x-circle")
            ))
            return None

    async def upload_audio_to_comfyui(self, audio_path: Path, generation_outputs: callable) -> Optional[str]:
        """Upload audio file to ComfyUI input directory and return the filename"""
        try:
            audio_bytes = audio_path.read_bytes()
            suffix = audio_path.suffix or '.mp3'
            content_type = {
                '.mp3': 'audio/mpeg',
                '.wav': 'audio/wav',
                '.flac': 'audio/flac',
                '.ogg': 'audio/ogg',
                '.m4a': 'audio/mp4',
                '.aac': 'audio/aac',
            }.get(suffix.lower(), 'audio/mpeg')

            protocol = "https" if self.config.get("secure", False) else "http"
            url = f"{protocol}://{self.config['host']}:{self.config['port']}/upload/image"

            data = aiohttp.FormData()
            data.add_field('image', audio_bytes, filename=audio_path.name, content_type=content_type)
            data.add_field('type', 'input')

            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        filename = result.get('name', audio_path.name)
                        logger.info(f"Uploaded audio to ComfyUI: {filename}")
                        return filename
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to upload audio: {response.status} - {error_text}")
                        generation_outputs(ProgressGenerationOutput(
                            state=f"Failed to upload audio: {response.status}",
                            icon=Icon("x-circle")
                        ))
                        return None

        except Exception as e:
            logger.error(f"Error uploading audio to ComfyUI: {e}")
            generation_outputs(ProgressGenerationOutput(
                state=f"Error uploading audio: {str(e)}",
                icon=Icon("x-circle")
            ))
            return None

    async def apply_field_mappings(self, workflow: Dict[str, Any], pipe_input: PipeInput, generation_outputs: callable) -> Dict[str, Any]:
        """Apply field mappings to the workflow"""
        workflow_copy = deepcopy(workflow)

        # Build name-to-ID mapping for node name resolution
        name_to_id, duplicates = self.build_node_name_mapping(workflow_copy)

        # Extract workflow node information for descriptive progress
        self.extract_workflow_info(workflow_copy)

        # Store seed from input if available
        seeds = pipe_input.input.get('seed', [])
        if seeds and len(seeds) > 0:
            self.current_seed = seeds[0]

        for mapping in self.config.get('field_mappings', []):
            if not mapping or len(mapping) < 2:
                logger.warning(f"Invalid mapping format: {mapping}")
                continue

            # The first element is the already-processed value from configuration
            value = mapping[0]
            target_path = mapping[1]
            type_cast = mapping[2] if len(mapping) > 2 else None

            # Check if value is a reference to input data (e.g., @input.seed or @seed)
            if isinstance(value, str) and value.startswith('@'):
                # Remove @ prefix and get the path
                input_path = value[1:]

                # Support both @input.seed and @seed syntax
                if input_path.startswith('input.'):
                    input_path = input_path[6:]  # Remove 'input.' prefix

                # Navigate the input path (support dot notation)
                path_parts = input_path.split('.')
                temp_value = pipe_input.input

                try:
                    for part in path_parts:
                        if isinstance(temp_value, dict):
                            temp_value = temp_value.get(part)
                        elif isinstance(temp_value, list) and part.isdigit():
                            temp_value = temp_value[int(part)]
                        else:
                            logger.warning(f"Cannot navigate path {input_path} in input data")
                            temp_value = None
                            break

                    # Handle list values - take first element for single values
                    if isinstance(temp_value, list) and len(temp_value) > 0:
                        value = temp_value[0]
                    else:
                        value = temp_value

                    logger.debug(f"Resolved input reference {mapping[0]} to {value}")

                except Exception as e:
                    logger.error(f"Failed to resolve input reference {mapping[0]}: {e}")
                    continue

            try:
                # Handle image type specially - upload to ComfyUI first
                if type_cast == "image" and value is not None:
                    if isinstance(value, str):
                        # Handle file path string
                        try:
                            # Load image from path
                            image_path = Path(value)
                            if not image_path.exists():
                                logger.error(f"Image file not found: {value}")
                                continue

                            # Open as PIL Image
                            pil_image = Image.open(image_path)

                            # Upload image to ComfyUI and get filename
                            filename = await self.upload_image_to_comfyui(pil_image, generation_outputs)
                            if filename:
                                value = filename
                            else:
                                logger.error(f"Failed to upload image for mapping {mapping}")
                                continue
                        except Exception as e:
                            logger.error(f"Failed to load image from path {value}: {e}")
                            continue
                    elif isinstance(value, Image.Image):
                        # Upload PIL Image to ComfyUI and get filename
                        filename = await self.upload_image_to_comfyui(value, generation_outputs)
                        if filename:
                            value = filename
                        else:
                            logger.error(f"Failed to upload image for mapping {mapping}")
                            continue
                    elif isinstance(value, dict) and 'data' in value:
                        # Handle image data from image field (contains bytes) - legacy format
                        try:
                            image_bytes = value['data']
                            if isinstance(image_bytes, bytes):
                                # Convert bytes to PIL Image
                                pil_image = Image.open(io.BytesIO(image_bytes))
                                # Upload image to ComfyUI and get filename
                                filename = await self.upload_image_to_comfyui(pil_image, generation_outputs)
                                if filename:
                                    value = filename
                                else:
                                    logger.error(f"Failed to upload image for mapping {mapping}")
                                    continue
                            else:
                                logger.warning(f"Expected bytes in image data, got {type(image_bytes)}")
                                continue
                        except Exception as e:
                            logger.error(f"Failed to process image data: {e}")
                            continue
                    else:
                        logger.warning(f"Expected file path string, PIL Image or image data dict for image type mapping, got {type(value)}")
                        continue
                elif type_cast == "video" and value is not None:
                    if isinstance(value, str):
                        try:
                            video_path = Path(value)
                            if not video_path.exists():
                                logger.error(f"Video file not found: {value}")
                                continue

                            filename = await self.upload_video_to_comfyui(video_path, generation_outputs)
                            if filename:
                                value = filename
                            else:
                                logger.error(f"Failed to upload video for mapping {mapping}")
                                continue
                        except Exception as e:
                            logger.error(f"Failed to upload video from path {value}: {e}")
                            continue
                    else:
                        logger.warning(f"Expected file path string for video type mapping, got {type(value)}")
                        continue
                elif type_cast == "audio" and value is not None:
                    if isinstance(value, str):
                        try:
                            audio_path = Path(value)
                            if not audio_path.exists():
                                logger.error(f"Audio file not found: {value}")
                                continue

                            filename = await self.upload_audio_to_comfyui(audio_path, generation_outputs)
                            if filename:
                                value = filename
                            else:
                                logger.error(f"Failed to upload audio for mapping {mapping}")
                                continue
                        except Exception as e:
                            logger.error(f"Failed to upload audio from path {value}: {e}")
                            continue
                    else:
                        logger.warning(f"Expected file path string for audio type mapping, got {type(value)}")
                        continue
                elif type_cast == "ltx_director_timeline" and value is not None:
                    # Build the LTXDirector 2.0 timeline_data JSON from a frontend
                    # timeline object (imageSegments + audioSegments). The upgraded
                    # node expects the full track-enabled structure: main-track image
                    # guides (each with start/length/isEndFrame/strength), an audio
                    # track, plus track-enabled flags and the render-window fields.
                    # value is expected to be a JSON string (rendered by Jinja).
                    try:
                        if isinstance(value, str):
                            data = json.loads(value)
                        elif isinstance(value, dict):
                            data = value
                        else:
                            raise ValueError(f"Unexpected timeline value type: {type(value)}")
                    except Exception as e:
                        logger.error(f"ltx_director_timeline: failed to parse value: {e}")
                        data = {}

                    fps = float(data.get('fps', 24) or 24)
                    duration = float(data.get('duration', 0) or 0)
                    duration_frames = max(1, int(round(duration * fps)))
                    global_prompt = str(data.get('global_prompt', '') or '')

                    segments_out: List[Dict[str, Any]] = []
                    audio_out: List[Dict[str, Any]] = []

                    # Resolve a media path that may be absolute, storage-relative
                    # (e.g. "storage/uploads/x.png"), or storage-prefixed.
                    def _resolve_media_path(media: Dict[str, Any]) -> Optional[Path]:
                        for candidate in (media.get('path'), media.get('relative_path')):
                            if not candidate:
                                continue
                            p = Path(candidate)
                            if p.exists():
                                return p
                            # try relative_path under the configured storage dir
                            storage_dir = None
                            try:
                                storage_dir = self.config.get('backend_config', {}).get('file_storage_directory')
                            except Exception:
                                storage_dir = None
                            for base in (storage_dir, 'storage'):
                                if base:
                                    pb = Path(base) / candidate
                                    if pb.exists():
                                        return pb
                        return None

                    # --- Image segments (main track) ---
                    # Embed as base64 (imageB64) rather than uploading a file: this works
                    # regardless of whether ComfyUI is local or remote, since the node
                    # decodes the bytes directly instead of reading its input directory.
                    # The frontend image lane is single-point (no span), so each guide is
                    # a 1-frame keyframe; isEndFrame is always false (no last-frame pin UI).
                    for img in data.get('imageSegments', []) or []:
                        media = img.get('media')
                        if not media:
                            continue
                        try:
                            image_path = _resolve_media_path(media)
                            if image_path is None:
                                logger.error(f"ltx_director_timeline: image file not found: {media.get('path') or media.get('relative_path')}")
                                continue
                            with open(image_path, 'rb') as f:
                                img_b64 = base64.b64encode(f.read()).decode('ascii')
                            start_frame = int(round(float(img.get('start', 0)) * fps))
                            segments_out.append({
                                "type": "image",
                                "imageB64": img_b64,
                                "start": start_frame,
                                "length": 1,
                                "isEndFrame": False,
                                "strength": float(img.get('strength', 1.0)),
                            })
                            logger.debug(f"ltx_director_timeline: embedded image ({len(img_b64)} b64 chars) at frame {start_frame}")
                        except Exception as e:
                            logger.error(f"ltx_director_timeline: error processing imageSegment: {e}")
                            continue

                    # --- Audio segments (audio track) ---
                    # The Director 2.0 audio latent noise-mask only PRESERVES regions
                    # backed by an uploaded file (audioFile); base64-embedded audio is
                    # regenerated away under inpaint_audio. So upload each clip and
                    # reference the returned ComfyUI input filename.
                    for aud in data.get('audioSegments', []) or []:
                        media = aud.get('media')
                        if not media:
                            continue
                        try:
                            audio_path = _resolve_media_path(media)
                            if audio_path is None:
                                logger.error(f"ltx_director_timeline: audio file not found: {media.get('path') or media.get('relative_path')}")
                                continue
                            audio_file = await self.upload_audio_to_comfyui(audio_path, generation_outputs)
                            if not audio_file:
                                logger.error("ltx_director_timeline: audio upload failed; skipping segment")
                                continue
                            start_frame = int(round(float(aud.get('start', 0)) * fps))
                            trim_start_frame = int(round(float(aud.get('trimStart', 0)) * fps))
                            length_frame = int(round(float(aud.get('length', 1)) * fps))
                            audio_out.append({
                                "audioFile": audio_file,
                                "start": start_frame,
                                "trimStart": trim_start_frame,
                                "length": length_frame,
                            })
                            logger.debug(f"ltx_director_timeline: uploaded audio '{audio_file}' at frame {start_frame}")
                        except Exception as e:
                            logger.error(f"ltx_director_timeline: error processing audioSegment: {e}")
                            continue

                    out: Dict[str, Any] = {
                        "global_prompt": global_prompt,
                        "mainTrackEnabled": True,
                        "audioTrackEnabled": len(audio_out) > 0,
                        "motionTrackEnabled": False,
                        "inpaint_audio": True,
                        "overrideAudio": False,
                        "normalStartFrame": 0,
                        "normalDurationFrames": duration_frames,
                        "segments": segments_out,
                        "motionSegments": [],
                        "audioSegments": audio_out,
                        "retakeMode": False,
                        "retake_global_prompt": global_prompt,
                    }

                    # Set the JSON string directly on the workflow node, bypassing cast_value.
                    self.set_nested_value(workflow_copy, target_path, json.dumps(out), name_to_id, duplicates)
                    logger.debug(f"ltx_director_timeline: set {target_path} -> {len(segments_out)} image seg(s), {len(audio_out)} audio seg(s)")
                    continue  # skip the generic set_nested_value below

                elif type_cast and value is not None:
                    # Type casting for non-image/video types
                    value = self.cast_value(value, type_cast)

                # Store seed value if it's being mapped
                if 'seed' in target_path.lower() or 'noise_seed' in target_path:
                    self.current_seed = value

                # Apply to workflow using dot notation (with node name resolution)
                self.set_nested_value(workflow_copy, target_path, value, name_to_id, duplicates)
                logger.debug(f"Applied mapping: {value} -> {target_path}")

            except Exception as e:
                logger.error(f"Failed to apply mapping {mapping}: {e}")

        return workflow_copy

    def evaluate_condition(self, condition, pipe_input: PipeInput) -> bool:
        """Evaluate a condition using Jinja2 templating"""
        # Handle boolean types first
        if isinstance(condition, bool):
            return condition

        # Handle None (template processing failure) - default to False for safety
        if condition is None:
            logger.warning("Condition evaluated to None (possible template error), defaulting to False")
            return False

        # Handle empty string (no condition specified) - default to True
        if condition == "":
            return True

        # Convert to string for string comparisons
        condition_str = str(condition).lower()

        if condition_str in ['true', '1', 'yes', 'on']:
            return True
        if condition_str in ['false', '0', 'no', 'off']:
            return False

        # Default to True for unknown conditions
        logger.warning(f"Unknown condition value '{condition}', defaulting to True")
        return True

    def remove_node(self, workflow: Dict[str, Any], node_id: str) -> None:
        """Remove a node from the workflow and clean up connections"""
        if node_id not in workflow:
            logger.warning(f"Node {node_id} not found in workflow")
            return

        logger.info(f"Removing node {node_id} from workflow")

        # Remove the node
        del workflow[node_id]

        # Clean up connections in other nodes that reference this node
        for other_node_id, node_data in workflow.items():
            if 'inputs' in node_data:
                inputs_to_update = {}
                for input_key, input_value in node_data['inputs'].items():
                    # Check if this input references the removed node
                    if isinstance(input_value, list) and len(input_value) == 2:
                        if str(input_value[0]) == node_id:
                            # This input references the removed node
                            # We'll set it to a default value or remove it
                            logger.warning(f"Node {other_node_id} input '{input_key}' referenced removed node {node_id}")
                            # Set to None or a default value depending on the input type
                            inputs_to_update[input_key] = None

                # Apply updates
                for key, value in inputs_to_update.items():
                    node_data['inputs'][key] = value

    def reroute_connection(self, workflow: Dict[str, Any], from_node: str, to_node: str,
                          new_target: str, output_index: int = 0) -> None:
        """Reroute connections from one node to another"""
        if to_node not in workflow:
            logger.warning(f"Target node {to_node} not found in workflow")
            return

        if new_target not in workflow:
            logger.warning(f"New target node {new_target} not found in workflow")
            return

        logger.info(f"Rerouting connections from {from_node} -> {to_node} to {from_node} -> {new_target}")

        # Find and update connections
        if 'inputs' in workflow[to_node]:
            for input_key, input_value in workflow[to_node]['inputs'].items():
                if isinstance(input_value, list) and len(input_value) == 2:
                    if str(input_value[0]) == from_node:
                        # This connection comes from the source node
                        # Reroute it to the new target
                        if new_target in workflow and 'inputs' in workflow[new_target]:
                            workflow[new_target]['inputs'][input_key] = input_value
                            logger.debug(f"Rerouted {to_node}.{input_key} to {new_target}.{input_key}")

    def bypass_node(self, workflow: Dict[str, Any], node_id: str) -> None:
        """Bypass a node by connecting its inputs directly to nodes that use its outputs"""
        if node_id not in workflow:
            logger.warning(f"Node {node_id} not found in workflow")
            return

        logger.info(f"Bypassing node {node_id}")

        # Find what this node connects to (its input)
        node_input_connection = None
        node_data = workflow[node_id]

        # Find the primary input connection (usually the first image/latent input)
        if 'inputs' in node_data:
            for input_key, input_value in node_data['inputs'].items():
                if isinstance(input_value, list) and len(input_value) == 2:
                    # This is a connection from another node
                    if input_key in ['images', 'samples', 'latent', 'frames', 'image']:
                        node_input_connection = input_value
                        break

        if not node_input_connection:
            logger.warning(f"No input connection found for node {node_id} to bypass - cannot bypass this node")
            # Still remove the node, but connections to it will be set to None
            found_connections = False
            for other_node_id, other_node_data in workflow.items():
                if other_node_id != node_id and 'inputs' in other_node_data:
                    for input_key, input_value in other_node_data['inputs'].items():
                        if isinstance(input_value, list) and len(input_value) == 2:
                            if str(input_value[0]) == node_id:
                                # This node uses our node's output, but we can't redirect it
                                other_node_data['inputs'][input_key] = None
                                found_connections = True
                                logger.warning(f"Cleared connection: {other_node_id}.{input_key} (was connected to {node_id})")

            # Only remove if we found connections or if explicitly trying to remove
            if found_connections:
                del workflow[node_id]
            return

        # Find all nodes that use this node's output and redirect them
        for other_node_id, other_node_data in workflow.items():
            if other_node_id != node_id and 'inputs' in other_node_data:
                for input_key, input_value in other_node_data['inputs'].items():
                    if isinstance(input_value, list) and len(input_value) == 2:
                        if str(input_value[0]) == node_id:
                            # This node uses our node's output
                            # Redirect it to use our node's input instead
                            other_node_data['inputs'][input_key] = node_input_connection
                            logger.debug(f"Bypassed connection: {other_node_id}.{input_key} now connects to node {node_input_connection[0]}")

        # Remove the bypassed node
        del workflow[node_id]

    def add_node(self, workflow: Dict[str, Any], node_id: str, node_config: Dict[str, Any]) -> None:
        """Add a new node to the workflow"""
        if node_id in workflow:
            logger.warning(f"Node {node_id} already exists, overwriting")

        workflow[node_id] = node_config
        logger.debug(f"Added node {node_id}: {node_config.get('class_type', 'Unknown')}")

    def update_node_input(self, workflow: Dict[str, Any], node_id: str, input_key: str, input_value: Any) -> None:
        """Update a specific input of a node"""
        if node_id not in workflow:
            logger.warning(f"Node {node_id} not found in workflow")
            return

        if 'inputs' not in workflow[node_id]:
            workflow[node_id]['inputs'] = {}

        workflow[node_id]['inputs'][input_key] = input_value
        logger.debug(f"Updated node {node_id} input '{input_key}' to {input_value}")

    def remove_node_input(self, workflow: Dict[str, Any], node_id: str, input_key: str) -> None:
        """Remove a specific input from a node"""
        if node_id not in workflow:
            logger.warning(f"Node {node_id} not found in workflow")
            return

        if 'inputs' not in workflow[node_id]:
            logger.warning(f"Node {node_id} has no inputs")
            return

        if input_key in workflow[node_id]['inputs']:
            del workflow[node_id]['inputs'][input_key]
            logger.debug(f"Removed input '{input_key}' from node {node_id}")
        else:
            logger.warning(f"Input '{input_key}' not found in node {node_id}")

    async def apply_node_manipulations(self, workflow: Dict[str, Any], pipe_input: PipeInput,
                                       generation_outputs: callable) -> Dict[str, Any]:
        """Apply node manipulations to the workflow"""
        manipulations = self.config.get('node_manipulations', [])

        if not manipulations:
            return workflow

        # Flatten one level so @loop-produced nested lists of manipulations work
        # the same as a flat list of manipulation dicts.
        manipulations = [
            m
            for entry in manipulations
            for m in (entry if isinstance(entry, list) else [entry])
        ]

        generation_outputs(ProgressGenerationOutput(
            state="Applying node manipulations",
            icon=Icon("git-branch"),
            progress=Progress(3, 100)
        ))

        workflow_copy = deepcopy(workflow)

        # Build name-to-ID mapping for node name resolution
        name_to_id, duplicates = self.build_node_name_mapping(workflow_copy)

        for manipulation in manipulations:
            if not isinstance(manipulation, dict):
                logger.warning(f"Invalid manipulation format: {manipulation}")
                continue

            manip_type = manipulation.get('type')
            condition = manipulation.get('condition', 'true')

            # Evaluate condition
            if not self.evaluate_condition(condition, pipe_input):
                logger.debug(f"Skipping {manip_type} due to condition: {condition}")
                continue

            try:
                if manip_type == 'remove_node':
                    node_id = str(manipulation.get('node_id'))
                    if node_id:
                        # Resolve node name to ID
                        resolved_node_id = self.resolve_node_reference(node_id, name_to_id, duplicates)
                        self.remove_node(workflow_copy, resolved_node_id)
                    else:
                        logger.warning(f"remove_node manipulation missing node_id")

                elif manip_type == 'reroute_connection':
                    from_node = str(manipulation.get('from_node'))
                    to_node = str(manipulation.get('to_node'))
                    new_target = str(manipulation.get('new_target'))
                    output_index = manipulation.get('output_index', 0)

                    if from_node and to_node and new_target:
                        # Resolve node names to IDs
                        resolved_from = self.resolve_node_reference(from_node, name_to_id, duplicates)
                        resolved_to = self.resolve_node_reference(to_node, name_to_id, duplicates)
                        resolved_target = self.resolve_node_reference(new_target, name_to_id, duplicates)
                        self.reroute_connection(workflow_copy, resolved_from, resolved_to,
                                              resolved_target, output_index)
                    else:
                        logger.warning(f"reroute_connection manipulation missing required parameters")

                elif manip_type == 'bypass_node':
                    node_id = str(manipulation.get('node_id'))
                    if node_id:
                        # Resolve node name to ID
                        resolved_node_id = self.resolve_node_reference(node_id, name_to_id, duplicates)
                        self.bypass_node(workflow_copy, resolved_node_id)
                    else:
                        logger.warning(f"bypass_node manipulation missing node_id")

                elif manip_type == 'add_node':
                    node_id = str(manipulation.get('node_id'))
                    node_config = manipulation.get('node_config')
                    if node_id and node_config:
                        # Resolve connection references in node_config inputs
                        resolved_config = self.resolve_connection_references(node_config, name_to_id, duplicates)
                        self.add_node(workflow_copy, node_id, resolved_config)
                        # Update name mapping with the new node (in case it has a title)
                        if '_meta' in resolved_config and 'title' in resolved_config['_meta']:
                            new_title = resolved_config['_meta']['title']
                            if new_title not in name_to_id and new_title not in duplicates:
                                name_to_id[new_title] = node_id
                    else:
                        logger.warning(f"add_node manipulation missing node_id or node_config")

                elif manip_type == 'update_node_input':
                    node_id = str(manipulation.get('node_id'))
                    input_key = manipulation.get('input_key')
                    input_value = manipulation.get('input_value')
                    type_cast = manipulation.get('type_cast')
                    if node_id and input_key is not None and input_value is not None:
                        # Resolve node name to ID
                        resolved_node_id = self.resolve_node_reference(node_id, name_to_id, duplicates)
                        # If input_value is a connection array, resolve it too
                        if isinstance(input_value, list) and len(input_value) == 2:
                            if isinstance(input_value[0], str) and isinstance(input_value[1], int):
                                resolved_ref = self.resolve_node_reference(input_value[0], name_to_id, duplicates)
                                input_value = [resolved_ref, input_value[1]]
                        # Apply type casting if specified (e.g., type_cast: "int" for ComfyUI nodes requiring native types)
                        if type_cast and input_value is not None:
                            input_value = self.cast_value(input_value, type_cast)
                        self.update_node_input(workflow_copy, resolved_node_id, input_key, input_value)
                    else:
                        logger.warning(f"update_node_input manipulation missing required parameters")

                elif manip_type == 'remove_node_input':
                    node_id = str(manipulation.get('node_id'))
                    input_key = manipulation.get('input_key')
                    if node_id and input_key:
                        # Resolve node name to ID
                        resolved_node_id = self.resolve_node_reference(node_id, name_to_id, duplicates)
                        self.remove_node_input(workflow_copy, resolved_node_id, input_key)
                    else:
                        logger.warning(f"remove_node_input manipulation missing node_id or input_key")

                else:
                    logger.warning(f"Unknown manipulation type: {manip_type}")

            except Exception as e:
                logger.error(f"Failed to apply {manip_type} manipulation: {e}")

        return workflow_copy

    async def connect_websocket(self, generation_outputs: callable) -> bool:
        """Connect to ComfyUI WebSocket"""
        try:
            protocol = "wss" if self.config.get("secure", False) else "ws"
            uri = f"{protocol}://{self.config['host']}:{self.config['port']}/ws"
            self.client_id = self.config.get('client_id') or str(uuid.uuid4())

            generation_outputs(ProgressGenerationOutput(
                state=f"Connecting to ComfyUI at {self.config['host']}:{self.config['port']}",
                icon=Icon("wifi"),
                progress=Progress(0, 100)
            ))

            self.ws = await asyncio.wait_for(
                websockets.connect(f"{uri}?clientId={self.client_id}"),
                timeout=10
            )

            logger.info(f"Connected to ComfyUI WebSocket with client ID: {self.client_id}")
            return True

        except asyncio.TimeoutError:
            logger.error(f"Timeout connecting to ComfyUI at {uri}")
            raise GenerationExecutionError(
                f"Could not connect to ComfyUI at {self.config['host']}:{self.config['port']}: "
                f"connection timed out"
            )
        except Exception as e:
            logger.error(f"Failed to connect to ComfyUI: {e}")
            raise GenerationExecutionError(
                f"Could not connect to ComfyUI at {self.config['host']}:{self.config['port']}: {e}"
            ) from e

    async def submit_workflow(self, workflow: Dict[str, Any], generation_outputs: callable) -> Optional[str]:
        """Submit workflow to ComfyUI API"""
        try:
            protocol = "https" if self.config.get("secure", False) else "http"
            url = f"{protocol}://{self.config['host']}:{self.config['port']}/prompt"

            # Count total nodes for progress tracking
            self.total_nodes = len(workflow)
            self.executed_nodes = 0

            prompt_data = {
                "prompt": workflow,
                "client_id": self.client_id
            }

            generation_outputs(ProgressGenerationOutput(
                state="Submitting workflow to ComfyUI",
                icon=Icon("send"),
                progress=Progress(5, 100)
            ))

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=prompt_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        prompt_id = result.get('prompt_id')
                        logger.info(f"Workflow submitted successfully with prompt ID: {prompt_id}")
                        return prompt_id
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to submit workflow: {response.status} - {error_text}")
                        raise GenerationExecutionError(
                            f"ComfyUI rejected the workflow submission (HTTP {response.status}): {error_text}"
                        )

        except GenerationExecutionError:
            raise
        except Exception as e:
            logger.error(f"Error submitting workflow: {e}")
            raise GenerationExecutionError(
                f"Could not submit workflow to ComfyUI at {self.config['host']}:{self.config['port']}: {e}"
            ) from e

    @staticmethod
    def _format_execution_error(error_data: Dict[str, Any]) -> str:
        """
        Build a human-readable body from ComfyUI's `execution_error` payload.

        ComfyUI supplies far more than the exception message - node id/type,
        exception type, and a full traceback list. This preserves that context
        as the expandable body of the failure notification.
        """
        lines: List[str] = []

        node_id = error_data.get('node_id')
        node_type = error_data.get('node_type')
        if node_id is not None or node_type:
            node_desc = f"Node {node_id}" if node_id is not None else "Node"
            if node_type:
                node_desc += f" ({node_type})"
            lines.append(node_desc)

        exc_type = error_data.get('exception_type')
        exc_message = error_data.get('exception_message')
        if exc_type or exc_message:
            lines.append(f"{exc_type or 'Error'}: {exc_message or ''}".strip())

        traceback_lines = error_data.get('traceback')
        if isinstance(traceback_lines, list) and traceback_lines:
            lines.append("")
            lines.extend(str(t).rstrip('\n') for t in traceback_lines)
        elif traceback_lines:
            lines.append("")
            lines.append(str(traceback_lines))

        return "\n".join(lines).strip() or None

    async def listen_for_updates(self, prompt_id: str, generation_outputs: callable) -> tuple[List[Image.Image], List[str]]:
        """Listen for workflow execution updates via WebSocket"""
        images = []
        videos = []

        try:
            while self.ws:
                message = await asyncio.wait_for(
                    self.ws.recv(),
                    timeout=self.config.get('timeout', 300)
                )

                # Handle binary messages (preview images from ComfyUI)
                if isinstance(message, bytes):
                    # ComfyUI binary message format:
                    # - First 4 bytes: message type as big-endian uint32 (1 = preview image, 2 = latent preview)
                    # - Next 4 bytes: format type as big-endian uint32 (1 = JPEG, 2 = PNG)
                    # - Remaining bytes: image data
                    if len(message) > 8:
                        # Parse message type as 4-byte big-endian integer
                        msg_type = int.from_bytes(message[0:4], byteorder='big')
                        format_type = int.from_bytes(message[4:8], byteorder='big')

                        if msg_type in (1, 2):
                            # This is a binary preview image
                            image_data = message[8:]  # Skip the 8-byte header
                            try:
                                preview_image = Image.open(io.BytesIO(image_data))
                                logger.debug(f"Received binary preview image: {preview_image.size}, format_type={format_type}")
                                generation_outputs(ImageGenerationOutput(
                                    image=preview_image,
                                    temporary=True  # Preview images are temporary
                                ))
                            except Exception as img_error:
                                logger.warning(f"Failed to parse binary preview image: {img_error}")
                            continue

                    # Try to decode as text if not a recognized binary format
                    try:
                        message = message.decode('utf-8')
                    except UnicodeDecodeError:
                        logger.debug(f"Skipping unrecognized binary message: {len(message)} bytes")
                        continue
                elif not isinstance(message, str):
                    logger.warning(f"Received unexpected message type: {type(message)}")
                    continue

                try:
                    data = json.loads(message)
                except json.JSONDecodeError as e:
                    logger.debug(f"Skipping non-JSON message ({len(message)} chars)")
                    continue
                msg_type = data.get('type')

                if msg_type == 'executing':
                    msg_data = data.get('data', {})
                    node_id = msg_data.get('node')

                    if node_id:
                        self.current_node = node_id
                        self.executed_nodes += 1
                        progress_percent = int((self.executed_nodes / self.total_nodes) * 100)

                        # Create descriptive message using workflow information
                        descriptive_message = self.get_descriptive_node_message(
                            node_id, self.executed_nodes, self.total_nodes
                        )

                        generation_outputs(ProgressGenerationOutput(
                            state=descriptive_message,
                            icon=Icon("play"),
                            progress=Progress(progress_percent, 100)
                        ))
                    elif msg_data.get('node') is None:
                        # Execution finished
                        generation_outputs(ProgressGenerationOutput(
                            state="Workflow execution completed",
                            icon=Icon("check-circle"),
                            progress=Progress(100, 100)
                        ))
                        break

                elif msg_type == 'executed':
                    # Handle output from executed node
                    msg_data = data.get('data', {})
                    output = msg_data.get('output', {})

                    # Handle videos from 'gifs' field (ComfyUI returns videos here)
                    if output and 'gifs' in output:
                        for video_data in output['gifs']:
                            video_path = await self.download_file_from_comfy(video_data, 'video')
                            if video_path:
                                videos.append(video_path)
                                # Extract metadata from ComfyUI response
                                frame_rate = video_data.get('frame_rate', 30.0)
                                format_info = video_data.get('format', 'video/mp4')
                                logger.info(f"Processing ComfyUI video: {video_data.get('filename')} at {frame_rate}fps, format: {format_info}")
                                generation_outputs(VideoGenerationOutput(
                                    video_path=video_path,
                                    temporary=True,
                                    fps=frame_rate,
                                    seed=self.current_seed if hasattr(self, 'current_seed') else None
                                ))

                    if output and 'images' in output:
                        for image_data in output['images']:
                            # Check if this is a video file
                            filename = image_data.get('filename', '')
                            if filename.lower().endswith(('.mp4', '.webm', '.avi', '.mov', '.mkv')):
                                # Handle as video
                                video_path = await self.download_file_from_comfy(image_data, 'video')
                                if video_path:
                                    videos.append(video_path)
                                    generation_outputs(VideoGenerationOutput(
                                        video_path=video_path,
                                        temporary=False,
                                        seed=self.current_seed if hasattr(self, 'current_seed') else None
                                    ))
                            else:
                                # Handle as image
                                image = await self.load_image_from_comfy(image_data)
                                if image:
                                    images.append(image)
                                    # Mark as non-temporary so it gets saved
                                    generation_outputs(ImageGenerationOutput(
                                        image=image,
                                        temporary=True,
                                        seed=self.current_seed if hasattr(self, 'current_seed') else None
                                    ))

                elif msg_type == 'progress':
                    # Handle preview/intermediate images and videos during generation
                    msg_data = data.get('data', {})
                    current_step = msg_data.get('value', 0)
                    max_steps = msg_data.get('max', 1)

                    # Emit step-level sampling progress
                    step_progress_percent = int((current_step / max_steps) * 100) if max_steps > 0 else 0

                    if self.current_node and self.current_node in self.workflow_nodes:
                        node_info = self.workflow_nodes[self.current_node]
                        title = node_info['title']
                        icon = self.get_node_icon(node_info['class_type'])
                        step_message = f"<<PIPE:{title}:{icon}>> Step {current_step}/{max_steps} <<PROGRESS:{step_progress_percent}%:chart>>"
                    else:
                        step_message = f"Sampling step {current_step}/{max_steps} <<PROGRESS:{step_progress_percent}%:chart>>"

                    generation_outputs(ProgressGenerationOutput(
                        state=step_message,
                        icon=Icon("bolt"),
                        progress=Progress(step_progress_percent, 100)
                    ))

                    if msg_data and 'output' in msg_data and msg_data['output']:
                        # Handle preview videos from 'gifs' field
                        if 'gifs' in msg_data['output']:
                            for video_data in msg_data['output']['gifs']:
                                video_path = await self.download_file_from_comfy(video_data, 'video')
                                if video_path:
                                    # Extract metadata from ComfyUI response
                                    frame_rate = video_data.get('frame_rate', 30.0)
                                    format_info = video_data.get('format', 'video/mp4')
                                    logger.info(f"Processing intermediate ComfyUI video: {video_data.get('filename')} at {frame_rate}fps, format: {format_info}")
                                    generation_outputs(VideoGenerationOutput(
                                        video_path=video_path,
                                        temporary=True,  # Intermediate videos are temporary
                                        fps=frame_rate,
                                        seed=self.current_seed if hasattr(self, 'current_seed') else None
                                    ))

                        # Handle preview images and videos from 'images' field
                        if 'images' in msg_data['output']:
                            for image_data in msg_data['output']['images']:
                                filename = image_data.get('filename', '')
                                if filename.lower().endswith(('.mp4', '.webm', '.avi', '.mov', '.mkv')):
                                    # Handle as intermediate video
                                    video_path = await self.download_file_from_comfy(image_data, 'video')
                                    if video_path:
                                        generation_outputs(VideoGenerationOutput(
                                            video_path=video_path,
                                            temporary=True,  # Intermediate videos are temporary
                                            seed=self.current_seed if hasattr(self, 'current_seed') else None
                                        ))
                                else:
                                    # Handle as intermediate image
                                    preview_image = await self.load_image_from_comfy(image_data)
                                    if preview_image:
                                        generation_outputs(ImageGenerationOutput(
                                            image=preview_image,
                                            temporary=True  # Preview images are temporary
                                        ))

                elif msg_type == 'execution_error':
                    error_data = data.get('data', {})
                    error_msg = error_data.get('exception_message', 'Unknown error')
                    logger.error(f"Execution error: {error_msg}")
                    # Surface a real failure (not a swallowed progress line) so
                    # GenerationEngine transitions the generation to FAILED and
                    # the user gets a notification. Preserve ComfyUI's rich error
                    # payload (node + traceback) as the notification body.
                    detail = self._format_execution_error(error_data)
                    raise GenerationExecutionError(error_msg, detail=detail)

                elif msg_type == 'execution_cached':
                    # Node was cached, still counts as progress
                    msg_data = data.get('data', {})
                    nodes = msg_data.get('nodes', [])
                    self.executed_nodes += len(nodes)
                    progress_percent = int((self.executed_nodes / self.total_nodes) * 100)

                    generation_outputs(ProgressGenerationOutput(
                        state=f"Using cached results ({self.executed_nodes}/{self.total_nodes})",
                        icon=Icon("database"),
                        progress=Progress(progress_percent, 100)
                    ))

        except GenerationExecutionError:
            # A real ComfyUI execution failure - let it propagate to
            # GenerationEngine instead of being swallowed as progress.
            raise
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for ComfyUI updates")
            raise GenerationExecutionError(
                f"Timed out waiting for ComfyUI to respond "
                f"(no update for {self.config.get('timeout', 300)}s)"
            )
        except Exception as e:
            logger.error(f"Error listening for updates: {e}")
            raise GenerationExecutionError(f"Error while receiving ComfyUI updates: {e}") from e

        return images, videos

    async def load_image_from_comfy(self, image_data: Dict[str, Any]) -> Optional[Image.Image]:
        """Load image from ComfyUI output"""
        try:
            filename = image_data.get('filename')
            subfolder = image_data.get('subfolder', '')
            image_type = image_data.get('type', 'output')

            protocol = "https" if self.config.get("secure", False) else "http"
            url = f"{protocol}://{self.config['host']}:{self.config['port']}/view"

            params = {
                'filename': filename,
                'type': image_type
            }
            if subfolder:
                params['subfolder'] = subfolder

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        image_bytes = await response.read()
                        return Image.open(io.BytesIO(image_bytes))
                    else:
                        logger.error(f"Failed to load image from ComfyUI: {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Error loading image from ComfyUI: {e}")
            return None

    async def download_file_from_comfy(self, file_data: Dict[str, Any], file_type: str) -> Optional[Path]:
        """Download file (video/image) from ComfyUI output"""
        try:
            filename = file_data.get('filename')
            subfolder = file_data.get('subfolder', '')
            output_type = file_data.get('type', 'output')

            protocol = "https" if self.config.get("secure", False) else "http"
            url = f"{protocol}://{self.config['host']}:{self.config['port']}/view"

            params = {
                'filename': filename,
                'type': output_type
            }
            if subfolder:
                params['subfolder'] = subfolder

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        # Create temporary file to save the video
                        import tempfile
                        file_extension = Path(filename).suffix or ('.mp4' if file_type == 'video' else '.png')

                        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
                            temp_path = Path(temp_file.name)

                        # Download and save the file
                        file_bytes = await response.read()
                        with open(temp_path, 'wb') as f:
                            f.write(file_bytes)
                            f.flush()  # Ensure all data is written to disk
                            os.fsync(f.fileno())  # Force write to disk

                        # Verify file is completely written and accessible
                        import time
                        max_attempts = 5
                        attempt = 0
                        while attempt < max_attempts:
                            try:
                                actual_size = os.path.getsize(temp_path)
                                expected_size = len(file_bytes)

                                if actual_size != expected_size:
                                    logger.warning(f"File size mismatch after download: expected {expected_size}, got {actual_size}, attempt {attempt + 1}")
                                    time.sleep(0.1)
                                    attempt += 1
                                    continue

                                # Try to read a small portion to verify accessibility
                                with open(temp_path, 'rb') as verify_f:
                                    verify_f.read(min(1024, actual_size))

                                logger.info(f"Downloaded {file_type} from ComfyUI: {filename} -> {temp_path} (size: {actual_size} bytes)")
                                return temp_path

                            except (OSError, IOError) as e:
                                logger.warning(f"File verification error: {e}, attempt {attempt + 1}")
                                time.sleep(0.1)
                                attempt += 1

                        # If we get here, verification failed
                        logger.error(f"Failed to verify downloaded file after {max_attempts} attempts")
                        return temp_path  # Return anyway, might still work
                    else:
                        logger.error(f"Failed to download {file_type} from ComfyUI: {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Error downloading {file_type} from ComfyUI: {e}")
            return None

    async def disconnect_websocket(self):
        """Disconnect WebSocket connection"""
        if self.ws:
            try:
                await self.ws.close()
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")
            finally:
                self.ws = None

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        """Main process method for the pipe"""
        try:
            # Check if backend config was injected (from ComfyUIBackend)
            # Backend config is injected into self.config['backend_config'] by the backend
            backend_config = self.config.get('backend_config', {})

            # Get default config values for comparison
            defaults = self.get_default_config()

            logger.debug(f"[COMFYUI_PIPE] Current config before merge: host={self.config.get('host')}, port={self.config.get('port')}")
            logger.debug(f"[COMFYUI_PIPE] Backend config: {backend_config}")

            if backend_config:
                # Backend config should only override DEFAULT values, not preset-specified values
                # This allows presets to explicitly configure connection settings when needed,
                # while still using backend defaults for standard deployments

                # Only apply backend host if preset uses the default value
                if backend_config.get('host') and self.config.get('host') == defaults.get('host'):
                    self.config['host'] = backend_config['host']
                    logger.debug(f"[COMFYUI_PIPE] Using backend host: {backend_config['host']}")

                # Only apply backend port if preset uses the default value
                if backend_config.get('port') and self.config.get('port') == defaults.get('port'):
                    self.config['port'] = backend_config['port']
                    logger.debug(f"[COMFYUI_PIPE] Using backend port: {backend_config['port']}")

                # Only apply backend secure if preset uses the default value (False or None)
                if backend_config.get('secure') is not None and self.config.get('secure') in (None, defaults.get('secure')):
                    self.config['secure'] = backend_config['secure']

                # Only apply backend client_id if preset doesn't specify one
                if backend_config.get('client_id') and not self.config.get('client_id'):
                    self.config['client_id'] = backend_config['client_id']

                # Only apply backend timeout if preset uses the default value
                if backend_config.get('timeout') and self.config.get('timeout') == defaults.get('timeout'):
                    self.config['timeout'] = backend_config['timeout']

                logger.debug(f"[COMFYUI_PIPE] Final config: host={self.config.get('host')}, port={self.config.get('port')}")

            # Run async process in event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                images, videos = loop.run_until_complete(
                    self._async_process(pipe_input, generation_outputs)
                )
                # Return both images and videos
                return PipeOutput(output={"image": images, "video": videos})
            finally:
                loop.close()

        except GenerationExecutionError:
            # Propagate real execution failures so the generation is marked
            # FAILED (and the user notified) rather than returning empty output.
            raise
        except Exception as e:
            logger.error(f"ComfyUI pipe error: {e}")
            raise GenerationExecutionError(f"ComfyUI pipe error: {e}") from e

    async def _async_process(self, pipe_input: PipeInput, generation_outputs: callable) -> tuple[List[Image.Image], List[str]]:
        """Async process implementation"""
        images = []
        videos = []

        try:
            # Load workflow
            generation_outputs(ProgressGenerationOutput(
                state="Loading workflow file",
                icon=Icon("file-text"),
                progress=Progress(0, 100)
            ))
            workflow = self.load_workflow_file()

            # Apply field mappings
            generation_outputs(ProgressGenerationOutput(
                state="Applying field mappings",
                icon=Icon("sliders"),
                progress=Progress(2, 100)
            ))
            workflow = await self.apply_field_mappings(workflow, pipe_input, generation_outputs)

            # Apply node manipulations (remove, bypass, reroute nodes)
            workflow = await self.apply_node_manipulations(workflow, pipe_input, generation_outputs)

            # Emit the final workflow for debugging/display
            generation_outputs(ComfyUIWorkflowGenerationOutput(
                workflow=workflow,
                node_count=len(workflow),
                workflow_file=self.config.get('workflow_file', '')
            ))

            # Connect to ComfyUI
            await self.connect_websocket(generation_outputs)

            # Submit workflow
            prompt_id = await self.submit_workflow(workflow, generation_outputs)

            # Listen for updates and collect images and videos
            images, videos = await self.listen_for_updates(prompt_id, generation_outputs)

            # A workflow that ran to completion without an execution_error but
            # produced nothing is still a failure, not a silent empty success -
            # otherwise it's indistinguishable from a real generation.
            if not images and not videos:
                raise GenerationExecutionError(
                    f"ComfyUI workflow {prompt_id} completed but produced no images or videos"
                )

        except GenerationExecutionError:
            # Real execution failure - propagate (the finally still cleans up
            # the WebSocket) so GenerationEngine can fail the generation.
            raise
        except Exception as e:
            logger.error(f"Error in async process: {e}")
            raise GenerationExecutionError(f"ComfyUI generation failed: {e}") from e
        finally:
            # Clean up WebSocket connection
            await self.disconnect_websocket()

        return images, videos
