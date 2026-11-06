import type { OrganizeSubject } from '$lib/types/organize';

const FACT_ICONS: Record<string, string> = {
	model: 'cube',
	lora: 'lora',
	preset: 'layout-template',
	mode: 'sliders',
	media_kind: 'image',
	resolution: 'expand',
	aspect: 'crop',
	duration: 'clock',
	prompt: 'text-cursor-input',
	filename: 'document',
	tags: 'tag',
	detected_tags: 'scan-search',
	model_type: 'box',
	base_model: 'boxes',
	name: 'text-cursor-input',
	description: 'book-open',
	trigger_words: 'zap',
	source: 'cloud-download',
	file_size: 'database',
	attribute: 'sliders-horizontal'
};

const ACTION_ICONS: Record<string, string> = {
	add_to_collection: 'folder-plus',
	add_tags: 'tag',
	add_model_tags: 'tag',
	assign_model_group: 'group',
	set_model_type: 'box',
	'civitai-provider.fetch_info': 'cloud-download'
};

const SUBJECT_ICONS: Record<OrganizeSubject, string> = {
	generation: 'sparkles',
	upload: 'upload',
	model: 'cube'
};

const KIND_ICONS: Record<string, string> = {
	image: 'image',
	video: 'video',
	audio: 'audio',
	mesh: 'cube'
};

const FALLBACK_ICON = 'extension';

export function factIcon(key: string): string {
	return FACT_ICONS[key] ?? FALLBACK_ICON;
}

export function actionIcon(key: string): string {
	return ACTION_ICONS[key] ?? FALLBACK_ICON;
}

export function subjectIcon(subject: OrganizeSubject): string {
	return SUBJECT_ICONS[subject] ?? 'sparkles';
}

export function mediaKindIcon(value: string): string | null {
	return KIND_ICONS[value] ?? null;
}
