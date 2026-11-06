import { itemLimitFor, readAcceptedKinds, readMediaLoaderConfig } from '$lib/components/form-fields/mediaLoaderConfig';
import { buildEditedMediaItem } from '$lib/components/form-fields/mediaLoaderEdited';
import type { EditedMediaItem } from '$lib/services/api/media';
import { extractAllFields } from '$lib/form/reactions';
import type { GenerationTab } from '$lib/types/tabs';
import { MEDIA_FIELD_TYPES } from '../sendTargets';

export interface ReferenceField {
	name: string;
	title: string;
	max: number | null;
}

export interface ReferenceTarget {
	id: string;
	tabId: string;
	field: ReferenceField;
	name: string;
	detail: string;
	color?: string;
	free: number | null;
	takes: number;
}

export function referenceFieldsOf(schema: unknown): ReferenceField[] {
	const fields: ReferenceField[] = [];
	const seen = new Set<string>();
	for (const field of extractAllFields(schema)) {
		const record = field as unknown as Record<string, unknown>;
		const name = typeof record.name === 'string' ? record.name : '';
		const type = typeof record.type === 'string' ? record.type : '';
		if (!name || seen.has(name) || !MEDIA_FIELD_TYPES.has(type)) continue;
		const config = readMediaLoaderConfig(record);
		if (!config.multiple || !readAcceptedKinds(record).includes('image')) continue;
		seen.add(name);
		const title = typeof record.title === 'string' && record.title.trim() ? record.title.trim() : name;
		fields.push({ name, title, max: itemLimitFor(config, 'image') });
	}
	return fields;
}

function heldCount(value: unknown): number {
	return Array.isArray(value) ? value.length : 0;
}

const two = (n: number) => String(n).padStart(2, '0');

export function referenceTargetsFor(
	tabs: readonly GenerationTab[],
	fields: Record<string, ReferenceField[]>,
	presetNames: Record<string, string>,
	count: number
): ReferenceTarget[] {
	const targets: ReferenceTarget[] = [];
	for (const tab of tabs) {
		const preset = tab.selectedPreset ? (presetNames[tab.selectedPreset] ?? null) : null;
		for (const field of fields[tab.id] ?? []) {
			const held = heldCount((tab.formData ?? {})[field.name]);
			const free = field.max === null ? null : Math.max(0, field.max - held);
			const takes = free === null ? count : Math.min(count, free);
			if (free === 0) continue;
			const label = preset ? `${preset} · ${field.title}` : field.title;
			const slots = free === null || field.max === null ? '' : ` · ${free} of ${field.max} slots free`;
			const partial = takes < count ? `, takes 01–${two(takes)}` : '';
			const target: ReferenceTarget = { id: `${tab.id}::${field.name}`, tabId: tab.id, field, name: tab.name, detail: `${label}${slots}${partial}`, free, takes };
			if (tab.color) target.color = tab.color;
			targets.push(target);
		}
	}
	return targets;
}

export function referencePatch(current: unknown, items: readonly EditedMediaItem[], takes: number): unknown[] {
	const held = Array.isArray(current) ? current : [];
	return [...held, ...items.slice(0, Math.max(0, takes)).map((item) => buildEditedMediaItem(item))];
}
