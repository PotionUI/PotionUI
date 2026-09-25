export type PromptResourceKind = 'image' | 'video' | 'audio';

export interface PromptResourceSpec {
	field: string;
	kind: PromptResourceKind;
	label?: string | null;
	token: string;
}

export interface ResourceRef {
	field: string;
	item_key: string;
}

export const RESOURCE_INDEX_PLACEHOLDER = '@';

export const RESOURCE_MARKER_SOURCE = "@\\[([A-Za-z_][A-Za-z0-9_-]*):([^\\]\\r\\n]+)\\]";

export function resourceMarkerRegex(): RegExp {
	return new RegExp(RESOURCE_MARKER_SOURCE, 'g');
}

export function encodeResourceMarker(field: string, itemKey: string): string {
	return `@[${field}:${itemKey}]`;
}

export function parseResourceMarker(marker: string): ResourceRef | null {
	const re = new RegExp(`^${RESOURCE_MARKER_SOURCE}$`);
	const match = re.exec(marker);
	if (!match) return null;
	return { field: match[1], item_key: match[2] };
}

const MEDIA_ITEM_KEY_FIELDS = ['relative_path', 'path', 'url'] as const;

export function mediaItemKey(item: unknown): string | null {
	if (typeof item === 'string') return item || null;
	if (item && typeof item === 'object') {
		for (const key of MEDIA_ITEM_KEY_FIELDS) {
			const value = (item as Record<string, unknown>)[key];
			if (typeof value === 'string' && value) return value;
		}
	}
	return null;
}

export function mediaFieldItems(value: unknown): unknown[] {
	if (value === null || value === undefined || value === '') return [];
	return Array.isArray(value) ? value : [value];
}

export function itemPosition(fieldValue: unknown, itemKey: string): number | null {
	const items = mediaFieldItems(fieldValue);
	for (let index = 0; index < items.length; index++) {
		if (mediaItemKey(items[index]) === itemKey) return index + 1;
	}
	return null;
}

export function itemAtPosition(fieldValue: unknown, position: number): unknown {
	const items = mediaFieldItems(fieldValue);
	return items[position - 1];
}

export function resourceGroupLabel(spec: PromptResourceSpec): string {
	if (spec.label) return spec.label;
	return `${kindLabel(spec.kind)}s`;
}

export function kindLabel(kind: PromptResourceKind): string {
	if (kind === 'image') return 'Picture';
	if (kind === 'video') return 'Video';
	return 'Audio';
}

export function resourceHandleLabel(spec: PromptResourceSpec, position: number): string {
	return `${kindLabel(spec.kind)} ${position}`;
}

export function renderResourceToken(spec: PromptResourceSpec, position: number): string {
	return spec.token.split(RESOURCE_INDEX_PLACEHOLDER).join(String(position));
}

export function findResourceSpec(
	specs: readonly PromptResourceSpec[],
	field: string
): PromptResourceSpec | undefined {
	return specs.find((spec) => spec.field === field);
}

export interface ResourceMarkerState {
	field: string;
	itemKey: string;
	spec: PromptResourceSpec | null;
	position: number | null;
	dangling: boolean;
}

export function resourceMarkerState(
	ref: ResourceRef,
	specs: readonly PromptResourceSpec[],
	formValues: Record<string, unknown>
): ResourceMarkerState {
	const spec = findResourceSpec(specs, ref.field) ?? null;
	if (!spec) {
		return { field: ref.field, itemKey: ref.item_key, spec: null, position: null, dangling: true };
	}
	const position = itemPosition(formValues[ref.field], ref.item_key);
	return { field: ref.field, itemKey: ref.item_key, spec, position, dangling: position === null };
}

export function resolveResourceMarkers(
	text: string,
	specs: readonly PromptResourceSpec[],
	formValues: Record<string, unknown>
): string {
	if (!text || !text.includes('@[')) return text;
	return text.replace(resourceMarkerRegex(), (full, field: string, itemKey: string) => {
		const spec = findResourceSpec(specs, field);
		if (!spec) return full;
		const position = itemPosition(formValues[field], itemKey);
		if (position === null) return full;
		return renderResourceToken(spec, position);
	});
}

export interface ResourceProblem {
	field: string;
	itemKey: string;
	reason: 'unmapped' | 'missing';
	message: string;
}

export function findResourceProblems(
	text: string,
	specs: readonly PromptResourceSpec[],
	formValues: Record<string, unknown>
): ResourceProblem[] {
	if (!text || !text.includes('@[')) return [];
	const problems: ResourceProblem[] = [];
	const re = resourceMarkerRegex();
	let match: RegExpExecArray | null;
	while ((match = re.exec(text)) !== null) {
		const field = match[1];
		const itemKey = match[2];
		const spec = findResourceSpec(specs, field);
		if (!spec) {
			problems.push({
				field,
				itemKey,
				reason: 'unmapped',
				message: `the prompt references '${field}', which this mode's prompt cannot reference`
			});
			continue;
		}
		if (itemPosition(formValues[field], itemKey) === null) {
			problems.push({
				field,
				itemKey,
				reason: 'missing',
				message: `the prompt references an item that was removed from this field (${itemKey})`
			});
		}
	}
	return problems;
}

export function collectResourceProblems(
	texts: readonly (string | null | undefined)[],
	specs: readonly PromptResourceSpec[],
	formValues: Record<string, unknown>
): ResourceProblem[] {
	const problems: ResourceProblem[] = [];
	for (const text of texts) {
		if (!text) continue;
		problems.push(...findResourceProblems(text, specs, formValues));
	}
	return problems;
}

export function textHasResourceMarkers(text: string | null | undefined): boolean {
	return typeof text === 'string' && text.includes('@[') && resourceMarkerRegex().test(text);
}

export function deriveResourcesFromText(
	text: string,
	previousResources: Record<string, ResourceRef>
): Record<string, ResourceRef> {
	const result: Record<string, ResourceRef> = {};
	if (!text || !text.includes('@[')) return result;

	const usedIds = new Set<string>();
	const re = resourceMarkerRegex();
	let match: RegExpExecArray | null;
	let counter = 0;

	while ((match = re.exec(text)) !== null) {
		const field = match[1];
		const itemKey = match[2];
		const existingEntry = Object.entries(previousResources).find(
			([id, ref]) => ref.field === field && ref.item_key === itemKey && !usedIds.has(id)
		);
		if (existingEntry) {
			const [id, ref] = existingEntry;
			usedIds.add(id);
			result[id] = ref;
		} else {
			const id = `res-${Date.now()}-${counter++}-${Math.random().toString(36).slice(2, 8)}`;
			result[id] = { field, item_key: itemKey };
		}
	}

	return result;
}
