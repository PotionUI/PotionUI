import { extractAllFields } from '$lib/form/reactions';
import { readMediaLoaderConfig } from '$lib/components/form-fields/mediaLoaderConfig';
import type { GenerationTab } from '$lib/types/tabs';
import type { SendTarget } from '$lib/tool-tabs/frame/types';

export interface VideoField {
	name: string;
	title: string;
	multiple: boolean;
}

export interface VideoSendTarget extends SendTarget {
	tabId: string;
	field: string;
	multiple: boolean;
}

export function videoFieldsOf(schema: unknown): VideoField[] {
	const fields: VideoField[] = [];
	const seen = new Set<string>();
	for (const field of extractAllFields(schema) as unknown as Array<Record<string, unknown>>) {
		const name = typeof field.name === 'string' ? field.name : '';
		if (!name || seen.has(name)) continue;
		const type = field.type;
		if (type !== 'video' && type !== 'media') continue;
		const limits = readMediaLoaderConfig(field.configuration ?? field);
		if (type === 'media' && !limits.kinds.includes('video')) continue;
		seen.add(name);
		const title = typeof field.title === 'string' && field.title.trim() ? field.title.trim() : name;
		fields.push({ name, title, multiple: limits.multiple === true });
	}
	return fields;
}

export function buildSendTargets(
	tabs: readonly GenerationTab[],
	fieldsByTab: Record<string, VideoField[]>
): VideoSendTarget[] {
	const targets: VideoSendTarget[] = [];
	for (const tab of tabs) {
		for (const field of fieldsByTab[tab.id] ?? []) {
			targets.push({
				id: `${tab.id}::${field.name}`,
				name: tab.name,
				detail: field.title,
				color: tab.color ?? undefined,
				tabId: tab.id,
				field: field.name,
				multiple: field.multiple
			});
		}
	}
	return targets;
}

export function withFieldValue(
	formData: Record<string, unknown>,
	target: Pick<VideoSendTarget, 'field' | 'multiple'>,
	value: Record<string, unknown>
): Record<string, unknown> {
	if (!target.multiple) return { ...formData, [target.field]: value };
	const current = formData[target.field];
	const list = Array.isArray(current) ? current : current ? [current] : [];
	return { ...formData, [target.field]: [...list, value] };
}
