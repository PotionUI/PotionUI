import { selectedCloudModelId } from '$lib/utils/cloudDirector';
import type { OrganizeCondition } from '$lib/types/organize';
import type { OrganizeStartRule } from './handoff';

const MEDIA_KINDS = ['image', 'video', 'audio', 'mesh'];

export interface GenerationItemLike {
	form_data?: Record<string, unknown> | null;
	files?: Array<{
		file_type?: string | null;
		is_final?: boolean;
		is_derived?: boolean;
		width?: number | null;
		height?: number | null;
	}> | null;
}

export function generationItemRule(item: GenerationItemLike): OrganizeStartRule {
	const conditions: OrganizeCondition[] = [];
	const modelId = selectedCloudModelId(item.form_data);
	if (modelId) conditions.push({ fact: 'model', operator: 'is', value: modelId });
	const files = item.files ?? [];
	const file = files.find((f) => f.is_final && !f.is_derived) ?? files.find((f) => f.is_final) ?? files[0];
	if (file?.file_type && MEDIA_KINDS.includes(file.file_type)) {
		conditions.push({ fact: 'media_kind', operator: 'is', value: file.file_type });
	}
	if (file?.width && file?.height) {
		conditions.push({ fact: 'resolution', operator: 'is', value: { width: file.width, height: file.height } });
	}
	return { subject: 'generation', conditions };
}

export interface UploadItemLike {
	media_type?: string | null;
	width?: number | null;
	height?: number | null;
}

export function uploadItemRule(item: UploadItemLike): OrganizeStartRule {
	const conditions: OrganizeCondition[] = [];
	if (item.media_type && MEDIA_KINDS.includes(item.media_type)) {
		conditions.push({ fact: 'media_kind', operator: 'is', value: item.media_type });
	}
	if (item.width && item.height) {
		conditions.push({ fact: 'resolution', operator: 'is', value: { width: item.width, height: item.height } });
	}
	return { subject: 'upload', conditions };
}

export interface ModelItemLike {
	model_type?: string | null;
	base_model?: string | null;
	family?: string | null;
}

export function modelItemRule(model: ModelItemLike): OrganizeStartRule {
	const conditions: OrganizeCondition[] = [];
	if (model.model_type) conditions.push({ fact: 'model_type', operator: 'is', value: model.model_type });
	const base = model.base_model ?? model.family;
	if (base) conditions.push({ fact: 'base_model', operator: 'is', value: base });
	return { subject: 'model', conditions };
}
