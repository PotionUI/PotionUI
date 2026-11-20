export interface HouseFormat {
	id: string;
	label: string;
	ratio: string;
	group_id: string | null;
}

export const SETTING_KEY = 'editor_expand_formats';
export const MAX_HOUSE_FORMATS = 40;

export function asFormats(value: unknown): HouseFormat[] {
	if (!Array.isArray(value)) return [];
	return value.flatMap((entry): HouseFormat[] => {
		if (typeof entry !== 'object' || entry === null) return [];
		const { id, label, ratio, group_id } = entry as Record<string, unknown>;
		if (typeof id !== 'string' || typeof label !== 'string' || typeof ratio !== 'string') return [];
		return [{ id, label, ratio, group_id: typeof group_id === 'string' && group_id ? group_id : null }];
	});
}

export function ratioOk(value: string): boolean {
	const match = /^\s*(\d+)\s*:\s*(\d+)\s*$/.exec(value);
	if (!match) return false;
	const width = Number(match[1]);
	const height = Number(match[2]);
	return width > 0 && height > 0 && width / height >= 1 / 8 && width / height <= 8;
}

export function normalRatio(value: string): string {
	const match = /^\s*(\d+)\s*:\s*(\d+)\s*$/.exec(value);
	return match ? `${Number(match[1])}:${Number(match[2])}` : value.trim();
}

export function slugOf(label: string, taken: readonly string[]): string {
	const base = label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) || 'format';
	if (!taken.includes(base)) return base;
	let count = 2;
	while (taken.includes(`${base}-${count}`)) count += 1;
	return `${base}-${count}`;
}

export function addFormat(list: readonly HouseFormat[], label: string, ratio: string, groupId: string | null): HouseFormat[] {
	if (list.length >= MAX_HOUSE_FORMATS) return [...list];
	const name = label.trim().slice(0, 40);
	if (!name || !ratioOk(ratio)) return [...list];
	return [...list, { id: slugOf(name, list.map((entry) => entry.id)), label: name, ratio: normalRatio(ratio), group_id: groupId }];
}

export function removeFormat(list: readonly HouseFormat[], id: string): HouseFormat[] {
	return list.filter((entry) => entry.id !== id);
}
