const DEFINITIONS = [
	{ id: 'checkpoint', label: 'Base model', virtual: false },
	{ id: 'diffusion_model', label: 'Diffusion model', virtual: false },
	{ id: 'lora', label: 'LoRA', virtual: false },
	{ id: 'embedding', label: 'Embedding', virtual: false },
	{ id: 'upscaler', label: 'Upscaler', virtual: false },
	{ id: 'vae', label: 'VAE', virtual: false },
	{ id: 'controlnet', label: 'ControlNet', virtual: false },
	{ id: 'adetailer', label: 'Detailer', virtual: false },
	{ id: 'text_encoder', label: 'Text encoder', virtual: false },
	{ id: 'unet', label: 'UNet', virtual: false },
	{ id: 'insightface', label: 'InsightFace', virtual: false },
	{ id: 'facerestore', label: 'Face restore', virtual: false },
	{ id: 'instantid', label: 'InstantID', virtual: false },
	{ id: 'detection_segm', label: 'Segmentation detector', virtual: false },
	{ id: 'detection_bbox', label: 'Box detector', virtual: false },
	{ id: 'mediapipe', label: 'MediaPipe', virtual: false },
	{ id: 'llm', label: 'LLM', virtual: false },
	{ id: 'vfi', label: 'VFI', virtual: false },
	{ id: 'model_patch', label: 'Model patch', virtual: false },
	{ id: 'geometry_estimation', label: 'Geometry estimator', virtual: false },
	{ id: 'preview_decoder', label: 'Preview decoder', virtual: false },
	{ id: 'pose_rig', label: 'Pose rig', virtual: false },
	{ id: 'cloud', label: 'Cloud', virtual: true }
];

export const SERVED_MODEL_TYPES = DEFINITIONS.filter((definition) => !definition.virtual).map((definition) => definition.id);

export function installHostModelTypes(definitions = DEFINITIONS) {
	globalThis.__potionui = {
		...(globalThis.__potionui ?? {}),
		modelTypes: {
			list: () => definitions.map((definition) => ({ ...definition })),
			get: (id) => definitions.find((definition) => definition.id === id)
		}
	};
}
