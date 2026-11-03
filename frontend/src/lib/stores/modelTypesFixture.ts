import type { ModelTypeDefinition } from '$lib/types/models';

export function modelTypeDefinition(overrides: Partial<ModelTypeDefinition> & { id: string }): ModelTypeDefinition {
	return {
		label: overrides.id,
		purpose: '',
		folder: overrides.id,
		aliases: [],
		extensions: ['.safetensors'],
		layout: 'file',
		header_classified: false,
		assignable: true,
		virtual: false,
		source: 'core',
		...overrides
	};
}

export function coreModelTypeDefinitions(): ModelTypeDefinition[] {
	return [
		modelTypeDefinition({ id: 'checkpoint', label: 'Base model', purpose: 'Main generation model', folder: 'checkpoints' }),
		modelTypeDefinition({ id: 'diffusion_model', label: 'Diffusion model', purpose: 'Main generation model', folder: 'diffusion_models' }),
		modelTypeDefinition({ id: 'lora', label: 'LoRA', purpose: 'Style or concept adapter', folder: 'loras' }),
		modelTypeDefinition({ id: 'text_encoder', label: 'Text encoder', purpose: 'Converts prompts into guidance', folder: 'text_encoders' }),
		modelTypeDefinition({ id: 'llm', label: 'LLM', purpose: 'Language model', layout: 'directory', assignable: false }),
		modelTypeDefinition({
			id: 'cloud',
			label: 'Cloud',
			purpose: 'Runs on a cloud service',
			folder: null,
			extensions: [],
			layout: null,
			assignable: false,
			virtual: true
		})
	];
}
