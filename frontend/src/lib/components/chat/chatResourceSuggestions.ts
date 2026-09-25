// @form.* suggestion builders for ChatChipInput.svelte, extracted unchanged
// — independent of the contenteditable core, served from local form
// state rather than the /api/chat/resources/suggest endpoint.
import type { PhrasebookCategory, PhrasebookValue } from '$lib/services/api/index';
import type { ResourceSuggestion } from '$lib/types/chat';

// A resource-suggest category that may also be directly attachable (see
// mapSuggestions) — AutocompleteDropdown reads `.attachable` to decide
// whether the row itself attaches or navigates.
export type MentionCategory = PhrasebookCategory & { attachable?: boolean };

export type MentionValue = PhrasebookValue & { badge?: string; description?: string; chip_label?: string };

export const MIN_BARE_SEARCH_CHARS = 2;

export function resourceChipLabel(item: { label: string; badge?: string; chip_label?: string }): string {
	if (item.chip_label) return item.chip_label;
	return item.badge ? `${item.badge}:${item.label}` : item.label;
}

export const FORM_ICON = 'sliders-horizontal';

type LoraRow = { id: string | null; name: string; strength: number };

// Maps ResourceSuggestions into the category/value shapes AutocompleteDropdown
// expects, so the dropdown component is reused unmodified. Navigable
// namespaces (has_children) become "categories"; leaves become "values"
// whose `value` field carries the full uri. A category marked
// `attachable` by the provider (e.g. a phrasebook category, resolvable
// at its own path) carries that flag through so the dropdown can offer
// "attach this" instead of forcing navigation.
export function mapSuggestions(suggestions: ResourceSuggestion[]): {
	child_categories: MentionCategory[];
	values: MentionValue[];
} {
	const child_categories: MentionCategory[] = [];
	const values: MentionValue[] = [];
	for (const s of suggestions) {
		if (s.has_children) {
			child_categories.push({
				id: s.uri,
				name: s.label,
				path: s.uri,
				description: s.description || '',
				is_active: true,
				created_at: '',
				updated_at: '',
				attachable: s.attachable ?? false
			});
		} else {
			values.push({
				id: s.uri,
				category_id: '',
				label: s.label,
				value: s.uri,
				sort_order: 0,
				is_active: true,
				created_at: '',
				updated_at: '',
				category_path: s.uri.includes('.') ? s.uri.substring(0, s.uri.lastIndexOf('.')) : s.uri,
				...(s.badge ? { badge: s.badge } : {}),
				...(s.badge && s.description ? { description: s.description } : {}),
				...(s.chip_label ? { chip_label: s.chip_label } : {})
			});
		}
	}
	return { child_categories, values };
}

export function isEmptyFormValue(value: any): boolean {
	if (value === null || value === undefined) return true;
	if (typeof value === 'string') return value.trim() === '';
	if (Array.isArray(value)) return value.length === 0;
	if (typeof value === 'object') return Object.keys(value).length === 0;
	return false;
}

export function formValuePreview(value: any): string | undefined {
	if (typeof value === 'string') {
		if (value.startsWith('model:')) return 'model';
		return value.length > 40 ? value.slice(0, 40) + '…' : value;
	}
	if (typeof value === 'number' || typeof value === 'boolean') return String(value);
	if (Array.isArray(value)) return `${value.length} item${value.length === 1 ? '' : 's'}`;
	return undefined;
}

export function buildLoraRowSuggestions(
	field: string,
	partial: string,
	loraSelections: Record<string, { id: string | null; name: string; strength: number }[]>
): ResourceSuggestion[] {
	const rows = loraSelections[field];
	if (!rows?.length) return [];
	const needle = partial.toLowerCase();
	const out: ResourceSuggestion[] = [];
	if (!needle) {
		out.push({
			uri: `form.${field}`,
			label: field,
			kind: 'lora_picker',
			description: `Attach all ${rows.length} selected LoRA${rows.length === 1 ? '' : 's'}`,
			has_children: true,
			attachable: true,
			icon: FORM_ICON
		});
	}
	rows.forEach((row, index) => {
		if (needle && !row.name.toLowerCase().includes(needle)) return;
		out.push(loraRowSuggestion(field, row, index));
	});
	return out.slice(0, 30);
}

function loraRowSuggestion(field: string, row: LoraRow, index: number): ResourceSuggestion {
	return {
		uri: `form.${field}.${row.id ?? String(index)}`,
		label: row.name,
		kind: 'lora',
		description: `strength ${row.strength}`,
		has_children: false,
		icon: FORM_ICON,
		badge: field,
		chip_label: `form.${field}:${row.name}`
	};
}

export function buildFormRowMatches(
	query: string,
	loraSelections: Record<string, LoraRow[]>
): ResourceSuggestion[] {
	const needle = query.trim().toLowerCase();
	if (needle.length < MIN_BARE_SEARCH_CHARS) return [];
	const out: ResourceSuggestion[] = [];
	for (const [field, rows] of Object.entries(loraSelections || {})) {
		(rows || []).forEach((row, index) => {
			if (row.name.toLowerCase().includes(needle)) out.push(loraRowSuggestion(field, row, index));
		});
	}
	return out.slice(0, 10);
}

export function buildFormSuggestions(
	partial: string,
	formData: Record<string, any>,
	loraSelections: Record<string, { id: string | null; name: string; strength: number }[]>
): ResourceSuggestion[] {
	const dot = partial.indexOf('.');
	if (dot >= 0) {
		return buildLoraRowSuggestions(partial.slice(0, dot), partial.slice(dot + 1), loraSelections);
	}
	const needle = partial.toLowerCase();
	const out: ResourceSuggestion[] = [];
	if (!needle || 'all'.startsWith(needle)) {
		out.push({
			uri: 'form',
			label: 'All form values',
			kind: 'form',
			description: 'Every non-empty field',
			has_children: false,
			icon: FORM_ICON
		});
	}
	for (const [name, value] of Object.entries(formData || {})) {
		if (isEmptyFormValue(value)) continue;
		if (needle && !name.toLowerCase().startsWith(needle)) continue;
		const loraRows = loraSelections[name];
		if (loraRows?.length) {
			out.push({
				uri: `form.${name}`,
				label: name,
				kind: 'lora_picker',
				description: `${loraRows.length} LoRA${loraRows.length === 1 ? '' : 's'} selected`,
				has_children: true,
				attachable: true,
				icon: FORM_ICON
			});
			continue;
		}
		out.push({
			uri: `form.${name}`,
			label: name,
			kind: 'form_value',
			description: formValuePreview(value),
			has_children: false,
			icon: FORM_ICON
		});
	}
	return out.slice(0, 30);
}

export function withFormRowMatches(
	path: string,
	mapped: { child_categories: MentionCategory[]; values: MentionValue[] },
	loraSelections: Record<string, LoraRow[]>
): { child_categories: MentionCategory[]; values: MentionValue[] } {
	if (path.includes('.')) return mapped;
	const rows = mapSuggestions(buildFormRowMatches(path, loraSelections)).values;
	if (!rows.length) return mapped;
	const taken = new Set(rows.map((r) => r.value));
	return { ...mapped, values: [...rows, ...mapped.values.filter((v) => !taken.has(v.value))] };
}
