import { oneOf, type FilterChip } from './librarySection';

export interface FilterFieldBase<F> {
	key: Extract<keyof F, string>;
	param: string;
	label: string;
	filterable?: boolean;
}

export interface EnumFilterField<F> extends FilterFieldBase<F> {
	kind: 'enum';
	values: readonly string[];
	default: string;
	chipKey?: string;
	chipLabel?: (value: string) => string | null;
}

export interface TextFilterField<F> extends FilterFieldBase<F> {
	kind: 'text';
	default?: string;
	chipKey?: string;
	chipLabel?: (value: string) => string | null;
}

export interface TagsFilterField<F> extends FilterFieldBase<F> {
	kind: 'tags';
	chipLabel?: (tag: string) => string;
}

export interface BooleanFilterField<F> extends FilterFieldBase<F> {
	kind: 'boolean';
	chipLabel?: string;
}

export type FilterFieldDescriptor<F> =
	| EnumFilterField<F>
	| TextFilterField<F>
	| TagsFilterField<F>
	| BooleanFilterField<F>;

export interface FilterBaseShape {
	q: string;
	sortBy: string;
}

export interface FilterCodecConfig<F extends FilterBaseShape> {
	defaults: F;
	fields: readonly FilterFieldDescriptor<F>[];
	sortValues: readonly string[];
}

export interface FilterCodec<F extends FilterBaseShape> {
	fromSearchParams(params: URLSearchParams): F;
	toSearchParams(filters: F): URLSearchParams;
	activeCount(filters: F): number;
	chips(filters: F, overrides?: Partial<Record<Extract<keyof F, string>, (value: never) => string | null>>): FilterChip[];
	clearChip(filters: F, key: string): F;
	clearAll(filters: F): F;
}

const TAG_CHIP_PREFIX = 'tag:';

export function createFilterCodec<F extends FilterBaseShape>(config: FilterCodecConfig<F>): FilterCodec<F> {
	const { defaults, fields, sortValues } = config;

	function fromSearchParams(params: URLSearchParams): F {
		const result = { ...defaults } as Record<string, unknown>;
		result.q = params.get('q') ?? defaults.q;
		result.sortBy = oneOf(params.get('sort_by'), sortValues, defaults.sortBy);
		for (const field of fields) {
			const raw = params.get(field.param);
			if (field.kind === 'enum') {
				result[field.key] = oneOf(raw, field.values, field.default);
			} else if (field.kind === 'text') {
				result[field.key] = raw ?? field.default ?? '';
			} else if (field.kind === 'tags') {
				result[field.key] = (raw ?? '')
					.split(',')
					.map((tag) => tag.trim())
					.filter(Boolean);
			} else if (field.kind === 'boolean') {
				result[field.key] = raw === '1';
			}
		}
		return result as F;
	}

	function toSearchParams(filters: F): URLSearchParams {
		const params = new URLSearchParams();
		if (filters.q) params.set('q', filters.q);
		for (const field of fields) {
			const value = (filters as Record<string, unknown>)[field.key];
			if (field.kind === 'enum') {
				if (value && value !== field.default) params.set(field.param, value as string);
			} else if (field.kind === 'text') {
				if (value && value !== (field.default ?? '')) params.set(field.param, value as string);
			} else if (field.kind === 'tags') {
				const tags = value as string[];
				if (tags.length) params.set(field.param, tags.join(','));
			} else if (field.kind === 'boolean') {
				if (value) params.set(field.param, '1');
			}
		}
		if (filters.sortBy !== defaults.sortBy) params.set('sort_by', filters.sortBy);
		return params;
	}

	function activeCount(filters: F): number {
		let count = 0;
		for (const field of fields) {
			if (field.filterable === false) continue;
			const value = (filters as Record<string, unknown>)[field.key];
			if (field.kind === 'tags') {
				if ((value as string[]).length) count++;
			} else if (field.kind === 'boolean') {
				if (value) count++;
			} else if (value && value !== (field as EnumFilterField<F> | TextFilterField<F>).default) {
				count++;
			}
		}
		return count;
	}

	function chips(
		filters: F,
		overrides?: Partial<Record<Extract<keyof F, string>, (value: never) => string | null>>
	): FilterChip[] {
		const result: FilterChip[] = [];
		for (const field of fields) {
			if (field.filterable === false) continue;
			const value = (filters as Record<string, unknown>)[field.key];
			const override = overrides?.[field.key] as ((value: unknown) => string | null) | undefined;
			if (field.kind === 'tags') {
				for (const tag of value as string[]) {
					const label = override ? override(tag) : field.chipLabel ? field.chipLabel(tag) : `#${tag}`;
					if (label) result.push({ key: `${TAG_CHIP_PREFIX}${tag}`, label });
				}
				continue;
			}
			if (field.kind === 'boolean') {
				if (value) result.push({ key: field.key, label: override ? (override(value) ?? '') : (field.chipLabel ?? field.key) });
				continue;
			}
			if (value && value !== (field as EnumFilterField<F> | TextFilterField<F>).default) {
				const label = override ? override(value) : field.chipLabel ? field.chipLabel(value as string) : (value as string);
				if (label) result.push({ key: field.chipKey ?? field.key, label });
			}
		}
		return result;
	}

	function clearChip(filters: F, key: string): F {
		if (key.startsWith(TAG_CHIP_PREFIX)) {
			const tag = key.slice(TAG_CHIP_PREFIX.length);
			const tagsField = fields.find((field) => field.kind === 'tags');
			if (!tagsField) return filters;
			const current = (filters as Record<string, unknown>)[tagsField.key] as string[];
			return { ...filters, [tagsField.key]: current.filter((entry) => entry !== tag) };
		}
		const field = fields.find((entry) => (('chipKey' in entry ? entry.chipKey : undefined) ?? entry.key) === key);
		if (!field) return filters;
		if (field.kind === 'boolean') return { ...filters, [field.key]: false };
		if (field.kind === 'enum') return { ...filters, [field.key]: field.default };
		if (field.kind === 'text') return { ...filters, [field.key]: field.default ?? '' };
		return filters;
	}

	function clearAll(filters: F): F {
		const result = { ...defaults, q: filters.q, sortBy: filters.sortBy } as unknown as Record<string, unknown>;
		for (const field of fields) {
			if (field.filterable === false) result[field.key] = (filters as Record<string, unknown>)[field.key];
		}
		return result as F;
	}

	return { fromSearchParams, toSearchParams, activeCount, chips, clearChip, clearAll };
}
