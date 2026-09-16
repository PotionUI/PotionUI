export interface TagsCategory {
	key: string;
	label: string;
	multi: boolean;
	allow_custom: boolean;
	tags: string[];
}

export type TagsMap = Record<string, string[]>;

const DEFAULT_SEPARATOR = ', ';

function dedupeTrim(values: unknown[]): string[] {
	const out: string[] = [];
	for (const raw of values) {
		if (typeof raw !== 'string') continue;
		const tag = raw.trim();
		if (!tag) continue;
		if (out.includes(tag)) continue;
		out.push(tag);
	}
	return out;
}

export function emptyTagsValue(categories: TagsCategory[]): TagsMap {
	const map: TagsMap = {};
	for (const cat of categories) map[cat.key] = [];
	return map;
}

function findMatchingCategory(token: string, categories: TagsCategory[]): TagsCategory | undefined {
	const lower = token.toLowerCase();
	return categories.find((cat) => cat.tags.some((t) => t.toLowerCase() === lower));
}

function parseLegacyString(raw: string, categories: TagsCategory[], separator: string): TagsMap {
	const map = emptyTagsValue(categories);
	if (categories.length === 0) return map;
	const lastKey = categories[categories.length - 1].key;
	const tokens = raw
		.split(separator)
		.map((t) => t.trim())
		.filter(Boolean);

	for (const token of tokens) {
		const matched = findMatchingCategory(token, categories);
		if (!matched) {
			if (!map[lastKey].includes(token)) map[lastKey].push(token);
			continue;
		}
		if (!matched.multi) {
			if (map[matched.key].length === 0) {
				map[matched.key].push(token);
			} else if (!map[lastKey].includes(token)) {
				map[lastKey].push(token);
			}
		} else if (!map[matched.key].includes(token)) {
			map[matched.key].push(token);
		}
	}

	return map;
}

export function normalizeTagsValue(
	value: unknown,
	categories: TagsCategory[],
	separator: string = DEFAULT_SEPARATOR
): TagsMap {
	if (typeof value === 'string') {
		return parseLegacyString(value, categories, separator);
	}
	const base = emptyTagsValue(categories);
	if (value && typeof value === 'object') {
		for (const cat of categories) {
			const raw = (value as Record<string, unknown>)[cat.key];
			if (Array.isArray(raw)) base[cat.key] = dedupeTrim(raw);
		}
	}
	return base;
}

export function joinTagsValue(
	value: TagsMap,
	categories: TagsCategory[],
	separator: string = DEFAULT_SEPARATOR
): string {
	const parts: string[] = [];
	for (const cat of categories) {
		for (const tag of value[cat.key] ?? []) parts.push(tag);
	}
	return parts.join(separator);
}

export function totalTagCount(value: TagsMap): number {
	return Object.values(value).reduce((sum, arr) => sum + arr.length, 0);
}

export function canAddMore(
	value: TagsMap,
	category: TagsCategory,
	maxTags: number | undefined
): boolean {
	if (maxTags == null) return true;
	if (!category.multi && (value[category.key]?.length ?? 0) > 0) return true;
	return totalTagCount(value) < maxTags;
}

export function addTag(
	value: TagsMap,
	categoryKey: string,
	rawTag: string,
	categories: TagsCategory[],
	maxTags?: number
): TagsMap {
	const category = categories.find((c) => c.key === categoryKey);
	if (!category) return value;
	const tag = rawTag.trim();
	if (!tag) return value;

	const existing = value[categoryKey] ?? [];
	if (existing.includes(tag)) return value;

	if (!category.multi) {
		if (existing.length === 0 && !canAddMore(value, category, maxTags)) return value;
		return { ...value, [categoryKey]: [tag] };
	}

	if (!canAddMore(value, category, maxTags)) return value;
	return { ...value, [categoryKey]: [...existing, tag] };
}

export function removeTag(value: TagsMap, categoryKey: string, tag: string): TagsMap {
	const existing = value[categoryKey] ?? [];
	if (!existing.includes(tag)) return value;
	return { ...value, [categoryKey]: existing.filter((t) => t !== tag) };
}

export function removeLastTag(value: TagsMap, categoryKey: string): TagsMap {
	const existing = value[categoryKey] ?? [];
	if (existing.length === 0) return value;
	return { ...value, [categoryKey]: existing.slice(0, -1) };
}

export function clearCategory(value: TagsMap, categoryKey: string): TagsMap {
	if (!(value[categoryKey]?.length > 0)) return value;
	return { ...value, [categoryKey]: [] };
}

export function toggleTag(
	value: TagsMap,
	categoryKey: string,
	tag: string,
	categories: TagsCategory[],
	maxTags?: number
): TagsMap {
	const existing = value[categoryKey] ?? [];
	if (existing.includes(tag)) return removeTag(value, categoryKey, tag);
	return addTag(value, categoryKey, tag, categories, maxTags);
}

export function filterCategoryTags(category: TagsCategory, query: string): string[] {
	const q = query.trim().toLowerCase();
	if (!q) return category.tags;
	return category.tags.filter((t) => t.toLowerCase().includes(q));
}

export function hasExactMatch(category: TagsCategory, query: string): boolean {
	const q = query.trim().toLowerCase();
	if (!q) return true;
	return category.tags.some((t) => t.toLowerCase() === q);
}

export function canAddCustom(fieldAllowCustom: boolean, category: TagsCategory): boolean {
	return category.allow_custom ?? fieldAllowCustom ?? true;
}
