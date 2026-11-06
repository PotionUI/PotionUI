import { readAcceptedKinds, readMediaLoaderConfig } from '$lib/components/form-fields/mediaLoaderConfig';
import { extractAllFields } from '$lib/form/reactions';
import type { SendTarget } from '$lib/tool-tabs/frame/types';
import type { GenerationTab } from '$lib/types/tabs';

export const MEDIA_FIELD_TYPES = new Set(['image', 'media']);

export interface ImageField {
	name: string;
	title: string;
	allowInpaint: boolean;
}

export function imageFieldsOf(schema: unknown): ImageField[] {
	const fields: ImageField[] = [];
	const seen = new Set<string>();
	for (const field of extractAllFields(schema)) {
		const record = field as unknown as Record<string, unknown>;
		const name = typeof record.name === 'string' ? record.name : '';
		const type = typeof record.type === 'string' ? record.type : '';
		if (!name || seen.has(name) || !MEDIA_FIELD_TYPES.has(type)) continue;
		const config = readMediaLoaderConfig(record);
		if (config.multiple || !readAcceptedKinds(record).includes('image')) continue;
		seen.add(name);
		const title = typeof record.title === 'string' && record.title.trim() ? record.title.trim() : name;
		fields.push({ name, title, allowInpaint: config.allowInpaint });
	}
	return fields;
}

export interface SendTargetEntry extends SendTarget {
	tabId: string;
	field: ImageField;
}

export function sendTargetId(tabId: string, field: string): string {
	return `${tabId}::${field}`;
}

export function sendTargetsFor(
	tabs: readonly GenerationTab[],
	fields: Record<string, ImageField[]>,
	presetNames: Record<string, string>,
	hasMask: boolean,
	first: { tabId: string; field: string } | null = null
): SendTargetEntry[] {
	const targets: SendTargetEntry[] = [];
	for (const tab of tabs) {
		const preset = tab.selectedPreset ? (presetNames[tab.selectedPreset] ?? null) : null;
		for (const field of fields[tab.id] ?? []) {
			const target: SendTargetEntry = {
				id: sendTargetId(tab.id, field.name),
				tabId: tab.id,
				field,
				name: tab.name,
				detail: preset ? `${preset} · ${field.title}` : field.title
			};
			if (hasMask && field.allowInpaint) target.badge = 'MASK';
			else if (tab.color) target.color = tab.color;
			targets.push(target);
		}
	}
	const lead = first ? targets.findIndex((target) => target.tabId === first.tabId && target.field.name === first.field) : -1;
	if (lead > 0) targets.unshift(...targets.splice(lead, 1));
	return targets;
}
