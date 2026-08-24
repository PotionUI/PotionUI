from pathlib import Path
from typing import Dict, Any, List, Optional
import torch

from src.pipelines.outputs import ProgressGenerationOutput, ModelGenerationOutput, ModelsGenerationOutput
from src.pipelines.contracts import BasePipe, logger
from src.pipelines.contracts import (
    PipeInput,
    PipeOutput,
    IOType,
    PipeInputSpec,
    PipeOutputSpec,
    PipeConfigSpec,
)


class ControlNetLoaderSDXLPipe(BasePipe):
    name = "controlnet_loader"
    description = "Load SDXL ControlNet models for guided generation"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "controlnets": [],  # List of ControlNet configurations
            "device": "cuda",
            "dtype": "float16",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec("controlnets", list, [], "List of ControlNet configurations", required=False),
            PipeConfigSpec("device", str, "cuda", "Device to load ControlNets on", required=False,
                          choices=["cuda", "cpu", "mps"]),
            PipeConfigSpec("dtype", str, "float16", "Data type for ControlNet weights", required=False,
                          choices=["float16", "float32", "bfloat16"]),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """ControlNetLoader uses the MODELS lifecycle service for caching"""
        return [
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service for cross-generation reuse", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """ControlNetLoader produces controlnet output"""
        return [
            PipeOutputSpec("controlnet", IOType.CONTROLNET, "Loaded ControlNet model(s)", is_array=True),
        ]

    def _load_controlnet(self, file_path: str, controlnet_config: Dict[str, Any]) -> Optional[Any]:
        """Load a single ControlNet model"""
        try:
            from diffusers import ControlNetModel

            controlnet_name = controlnet_config.get('name', Path(file_path).stem)
            controlnet_type = controlnet_config.get('type', 'unknown')

            logger.info(f"[CONTROLNET LOADER SDXL] Loading ControlNet: {controlnet_name} (type: {controlnet_type})")

            # Get device and dtype
            device = self.config.get("device", "cuda")
            dtype_str = self.config.get("dtype", "float16")

            # Convert dtype string to torch dtype
            dtype_map = {
                "float16": torch.float16,
                "float32": torch.float32,
                "bfloat16": torch.bfloat16,
            }
            dtype = dtype_map.get(dtype_str, torch.float16)

            # Load ControlNet from single file
            controlnet = ControlNetModel.from_single_file(
                file_path,
                torch_dtype=dtype,
                use_safetensors=True,
            )

            # Validate that this is an SDXL-compatible ControlNet
            # SDXL uses 2048-dimensional embeddings, SD1.5 uses 768-dimensional
            if hasattr(controlnet.config, 'cross_attention_dim'):
                cross_attn_dim = controlnet.config.cross_attention_dim
                if cross_attn_dim == 768:
                    raise ValueError(
                        f"[CONTROLNET LOADER SDXL] ControlNet '{controlnet_name}' is for SD1.5 (768-dim), not SDXL (2048-dim). "
                        f"Please use an SDXL-compatible ControlNet model. "
                        f"Popular SDXL ControlNet models: xinsir/controlnet-canny-sdxl-1.0, xinsir/controlnet-depth-sdxl-1.0"
                    )
                elif cross_attn_dim != 2048:
                    logger.warning(
                        f"[CONTROLNET LOADER SDXL] ControlNet cross_attention_dim={cross_attn_dim}, expected 2048 for SDXL. "
                        f"This may cause errors."
                    )
                else:
                    logger.debug(f"[CONTROLNET LOADER SDXL] ControlNet is SDXL-compatible (cross_attention_dim={cross_attn_dim})")

            # Move to device
            controlnet = controlnet.to(device)

            logger.info(f"[CONTROLNET LOADER SDXL] Successfully loaded ControlNet: {controlnet_name}")

            # Return a dict with the model and its configuration
            return {
                "model": controlnet,
                "name": controlnet_name,
                "type": controlnet_type,
                "file_path": file_path,
                "conditioning_scale": float(controlnet_config.get('conditioning_scale', 1.0)),
                "control_guidance_start": float(controlnet_config.get('control_guidance_start', 0.0)),
                "control_guidance_end": float(controlnet_config.get('control_guidance_end', 1.0)),
            }

        except Exception as e:
            logger.error(f"[CONTROLNET LOADER SDXL] Error loading ControlNet {file_path}: {e}")
            return None

    def process(
            self,
            pipe_input: PipeInput,
            generation_outputs: callable
    ) -> PipeOutput:
        controlnets_config = self.config.get("controlnets", [])

        if not controlnets_config:
            logger.debug("[CONTROLNET LOADER SDXL] No ControlNets configured")
            return PipeOutput(output={"controlnet": []})

        models = pipe_input.input.get("MODELS", None)
        fingerprint = f"{controlnets_config}|{self.config.get('device', 'cuda')}|{self.config.get('dtype', 'float16')}"

        def _load_controlnets():
            generation_outputs(ProgressGenerationOutput(state="Loading ControlNet models"))

            loaded_controlnets = []
            models_output = []

            for cn_config in controlnets_config:
                # Skip if not enabled or no file path
                if not cn_config.get('enabled', False):
                    continue

                file_path = cn_config.get('file_path')
                if not file_path or file_path == '':
                    continue

                # Check if file exists
                if not Path(file_path).exists():
                    logger.warning(f"[CONTROLNET LOADER SDXL] ControlNet file not found: {file_path}")
                    continue

                # Load the ControlNet
                controlnet_data = self._load_controlnet(file_path, cn_config)
                if controlnet_data:
                    loaded_controlnets.append(controlnet_data)

                    # Add to models output for UI display
                    models_output.append(ModelGenerationOutput(
                        name=controlnet_data['name'],
                        type="controlnet",
                        weight=controlnet_data['conditioning_scale']
                    ))

            if loaded_controlnets:
                logger.info(f"[CONTROLNET LOADER SDXL] Loaded {len(loaded_controlnets)} ControlNet(s)")
                generation_outputs(ModelsGenerationOutput(models=models_output))
                generation_outputs(ProgressGenerationOutput(
                    state=f"Loaded <<NUMBER:{len(loaded_controlnets)} ControlNet(s):check-circle>>"
                ))
            else:
                logger.warning("[CONTROLNET LOADER SDXL] No ControlNets were loaded")

            return loaded_controlnets

        if models is not None:
            loaded_controlnets = models.acquire(
                key="controlnet_loader/sdxl",
                fingerprint=fingerprint,
                loader=_load_controlnets,
            )
        else:
            loaded_controlnets = _load_controlnets()

        return PipeOutput(output={"controlnet": loaded_controlnets})
