export interface ModelTypePresentation {
	label: string;
	purpose: string;
}

const MODEL_TYPES: Record<string, ModelTypePresentation> = {
	checkpoint: { label: 'Base model', purpose: 'Main generation model' },
	diffusion_model: { label: 'Diffusion model', purpose: 'Main generation model' },
	lora: { label: 'LoRA', purpose: 'Style or concept adapter' },
	embedding: { label: 'Embedding', purpose: 'Prompt concept or style' },
	upscaler: { label: 'Upscaler', purpose: 'Resolution and detail enhancement' },
	vae: { label: 'VAE', purpose: 'Image encoder and decoder' },
	controlnet: { label: 'ControlNet', purpose: 'Structure and composition guidance' },
	adetailer: { label: 'Detailer', purpose: 'Automatic detail refinement' },
	clip: { label: 'Text encoder', purpose: 'Converts prompts into guidance' }
};

export function modelTypePresentation(modelType?: string | null): ModelTypePresentation {
	if (!modelType) return { label: 'Model', purpose: 'Generation resource' };
	const normalized = modelType.toLowerCase();
	return (
		MODEL_TYPES[normalized] || {
			label: normalized.replace(/_/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase()),
			purpose: 'Generation resource'
		}
	);
}

function readableProviderName(provider?: string | null): string {
	if (!provider) return '';
	return provider
		.replace(/[-_]provider$/i, '')
		.replace(/[-_]+/g, ' ')
		.replace(/\b\w/g, (character) => character.toUpperCase());
}

export function modelSourceLabel(model: any): string {
	return readableProviderName(model?.providers?.[0]?.provider);
}

export function modelFilenameStem(model: any): string {
	const filename = String(model?.filename || '').split(/[\\/]/).pop() || '';
	return filename.replace(/\.[^.]+$/, '');
}

export function modelTagLabels(model: any, limit = 2): string[] {
	const rawTags = [
		...(model?.tags || []).map((tag: any) => (typeof tag === 'string' ? tag : tag?.name)),
		...(model?.providers?.[0]?.tags || [])
	];
	const seen = new Set<string>();
	const result: string[] = [];
	for (const rawTag of rawTags) {
		const tag = String(rawTag || '').trim();
		if (!tag) continue;
		const key = tag.toLowerCase();
		if (seen.has(key)) continue;
		seen.add(key);
		result.push(tag);
		if (result.length === limit) break;
	}
	return result;
}

/** Compact, decision-oriented metadata used by picker rows and selected summaries. */
export function modelSummaryParts(model: any, tagLimit = 2): string[] {
	const type = modelTypePresentation(model?.model_type);
	return [type.label, modelSourceLabel(model), ...modelTagLabels(model, tagLimit)].filter(Boolean);
}
