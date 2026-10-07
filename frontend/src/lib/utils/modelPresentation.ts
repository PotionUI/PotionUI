export const MODEL_TYPE_UNDEFINED = 'undefined';

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
	text_encoder: { label: 'Text encoder', purpose: 'Converts prompts into guidance' },
	refmod: { label: 'RefMod', purpose: 'Pre-encoded reference latents' },
	model_patch: { label: 'Model patch', purpose: 'Add-on branch loaded with a diffusion model' },
	geometry_estimation: { label: 'Geometry estimator', purpose: 'Estimates depth and camera from an image' },
	[MODEL_TYPE_UNDEFINED]: { label: 'Needs a type', purpose: 'Type not recognised from the file' }
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
	if (String(model?.model_type || '').toLowerCase() === 'cloud') return '';
	return readableProviderName(model?.providers?.[0]?.provider);
}

export function modelFilenameStem(model: any): string {
	const filename = String(model?.filename || '').split(/[\\/]/).pop() || '';
	return filename.replace(/\.[^.]+$/, '');
}

export const CLOUD_MODEL_TYPE = 'cloud';

export function modelIsCloud(model: any): boolean {
	return String(model?.model_type || '').toLowerCase() === CLOUD_MODEL_TYPE;
}

function cloudProviderName(model: any): string {
	const label = typeof model?.provider_label === 'string' ? model.provider_label.trim() : '';
	if (label) return label;
	const driver = String(model?.providers?.[0]?.provider || '');
	const key = driver.includes('.') ? driver.slice(driver.lastIndexOf('.') + 1) : driver;
	return readableProviderName(key);
}

export function modelOriginLine(model: any): string {
	if (modelIsCloud(model)) {
		const provider = cloudProviderName(model);
		const vendor = typeof model?.vendor === 'string' ? model.vendor.trim() : '';
		const parts = [provider, vendor].filter(Boolean);
		return parts.length ? `Cloud · ${parts.join(' · ')}` : '';
	}
	const stem = modelFilenameStem(model);
	return stem ? `File · ${stem}` : '';
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
