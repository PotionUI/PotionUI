import type { FilterOption } from '../types';

export function uniqueOptions(values: readonly (string | null | undefined)[], anyLabel: string): FilterOption[] {
	const set = new Set<string>();
	for (const value of values) if (value) set.add(value);
	const sorted = [...set].sort((a, b) => a.localeCompare(b));
	return [{ value: '', label: anyLabel }, ...sorted.map((value) => ({ value, label: value }))];
}

export function byName<Row>(getName: (row: Row) => string) {
	return (a: Row, b: Row) => getName(a).localeCompare(getName(b), undefined, { sensitivity: 'base', numeric: true });
}

export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
	return `${count} ${count === 1 ? singular : pluralForm}`;
}

export function hostOf(url: string | null | undefined): string {
	if (!url) return '';
	try {
		return new URL(url).host;
	} catch {
		return url.replace(/^https?:\/\//, '').split('/')[0];
	}
}

export function initials(name: string): string {
	const parts = name.trim().split(/[\s._-]+/).filter(Boolean);
	if (parts.length === 0) return '?';
	if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
	return (parts[0][0] + parts[1][0]).toUpperCase();
}
