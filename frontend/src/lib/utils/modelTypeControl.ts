import { MODEL_TYPE_UNDEFINED, modelTypePresentation } from '$lib/utils/modelPresentation';

export const AUTOMATIC_TYPE = 'automatic';

export const ASSIGNABLE_MODEL_TYPES: readonly string[] = [
	'checkpoint',
	'diffusion_model',
	'lora',
	'embedding',
	'upscaler',
	'vae',
	'controlnet',
	'adetailer',
	'text_encoder',
	'unet',
	'insightface',
	'facerestore',
	'instantid',
	'detection_segm',
	'detection_bbox',
	'mediapipe',
	'vfi',
	'refmod'
];

export interface ModelTypeInfo {
	source: string;
	folder_type: string | null;
	family: string | null;
	variant: string | null;
	classifier: string | null;
	components: string[];
	verdict_status: string | null;
	packaging: string | null;
}

export type ModelTypeChange = { kind: 'none' } | { kind: 'set'; modelType: string } | { kind: 'reset' };

const ASSERTION_SOURCES = new Set(['admin', 'recipe', 'download']);
const ACRONYM_FAMILIES = new Set(['sd', 'sdxl', 'ltx']);

export function familyLabel(family?: string | null): string {
	if (!family) return '';
	return family
		.split('_')
		.filter(Boolean)
		.map((part) => (ACRONYM_FAMILIES.has(part) ? part.toUpperCase() : part.charAt(0).toUpperCase() + part.slice(1)))
		.join(' ');
}

export function typeSourceIsAssertion(info?: Pick<ModelTypeInfo, 'source'> | null): boolean {
	return !!info && ASSERTION_SOURCES.has(info.source);
}

export function typeSourceLine(info?: ModelTypeInfo | null, modelType?: string | null): string {
	if (modelType === MODEL_TYPE_UNDEFINED) return "The file header didn't match any known model. Choose its type.";
	if (!info) return '';
	switch (info.source) {
		case 'admin':
			return 'Set by an admin';
		case 'recipe':
			return 'Set by a recipe';
		case 'download':
			return 'Set when it was downloaded';
		case 'header': {
			const family = familyLabel(info.family);
			if (!family) return 'Detected from the file';
			return info.variant ? `Detected from the file: ${family} (${info.variant})` : `Detected from the file: ${family}`;
		}
		default:
			return 'From the folder it is in';
	}
}

export function typePackagingLine(info?: ModelTypeInfo | null): string {
	if (!info || info.packaging !== 'full_checkpoint') return '';
	const family = familyLabel(info.family);
	return family
		? `Full checkpoint, diffusion model used by ${family} pickers`
		: 'Full checkpoint, diffusion model used by pickers';
}

export function typeOptions(currentType: string): { value: string; label: string; disabled?: boolean }[] {
	const options = ASSIGNABLE_MODEL_TYPES.map((value) => ({ value, label: modelTypePresentation(value).label }));
	if (!ASSIGNABLE_MODEL_TYPES.includes(currentType)) {
		return [{ value: currentType, label: modelTypePresentation(currentType).label, disabled: true }, ...options];
	}
	return options;
}

export function typeChangeFromDraft(draft: string, currentType: string): ModelTypeChange {
	if (draft === AUTOMATIC_TYPE) return { kind: 'reset' };
	if (!draft || draft === currentType) return { kind: 'none' };
	return { kind: 'set', modelType: draft };
}
