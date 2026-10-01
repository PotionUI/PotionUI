import { extractAllFields, extractFieldDependencies } from '$lib/form/reactions';
import { getSchemaDefaults } from '$lib/form/defaults';

export interface IndexedField {
	name: string;
	label: string;
	type: string;
	options?: Array<{ label: string; value: unknown }>;
	advanced: boolean;
	controllers: string[];
	node: Record<string, any>;
}

export type FieldIndex = Map<string, IndexedField>;

const SKIPPED_TYPES = new Set(['tab', 'tabs', 'section', 'row', 'group', 'accordion', 'header', 'markdown', 'alert']);

export function indexFields(schema: unknown): FieldIndex {
	const index: FieldIndex = new Map();
	for (const node of extractAllFields(schema) as any[]) {
		if (!node?.name || SKIPPED_TYPES.has(node.type) || index.has(node.name)) continue;
		index.set(node.name, {
			name: node.name,
			label: node.title || node.label || node.name,
			type: node.type,
			options: Array.isArray(node.options) ? node.options : undefined,
			advanced: node.audience === 'advanced',
			controllers: extractFieldDependencies(node),
			node
		});
	}
	return index;
}

export function flattenValue(value: unknown): unknown {
	if (value && typeof value === 'object' && !Array.isArray(value) && 'modelPath' in (value as object)) {
		return (value as { modelPath: unknown }).modelPath;
	}
	return value;
}

export function flatDefaults(schema: unknown): Record<string, unknown> {
	const defaults = getSchemaDefaults(schema as any);
	const flat: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(defaults)) flat[key] = flattenValue(value);
	return flat;
}

export function dependencyMap(index: FieldIndex): Record<string, string[]> {
	const map: Record<string, string[]> = {};
	for (const field of index.values()) if (field.controllers.length > 0) map[field.name] = field.controllers;
	return map;
}
