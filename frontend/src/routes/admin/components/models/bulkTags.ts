import type {
	BulkModelTagsBody,
	BulkModelTagsResult,
	ModelSelectionTag
} from '$lib/services/api/models';

export interface KnownTag {
	id: string;
	name: string;
}

export interface SelectionTagRow {
	id: string;
	name: string;
	count: number;
	total: number;
	label: string;
	onAll: boolean;
}

const norm = (value: string) => value.trim().toLowerCase();

export function addChip(chips: string[], raw: string): string[] {
	const value = raw.trim();
	if (!value) return chips;
	if (chips.some((chip) => norm(chip) === norm(value))) return chips;
	return [...chips, value];
}

export function removeChip(chips: string[], value: string): string[] {
	return chips.filter((chip) => norm(chip) !== norm(value));
}

export function toggleRemoval(removeIds: string[], tagId: string): string[] {
	return removeIds.includes(tagId) ? removeIds.filter((id) => id !== tagId) : [...removeIds, tagId];
}

export function suggestTags(known: KnownTag[], query: string, chips: string[], limit = 8): KnownTag[] {
	const q = norm(query);
	const taken = new Set(chips.map(norm));
	const pool = known.filter((tag) => !taken.has(norm(tag.name)));
	if (!q) return pool.slice(0, limit);
	const starts = pool.filter((tag) => norm(tag.name).startsWith(q));
	const contains = pool.filter((tag) => !norm(tag.name).startsWith(q) && norm(tag.name).includes(q));
	return [...starts, ...contains].slice(0, limit);
}

export function selectionRows(tags: ModelSelectionTag[], total: number): SelectionTagRow[] {
	const merged = new Map<string, ModelSelectionTag>();
	for (const tag of tags) {
		const prev = merged.get(tag.id);
		merged.set(tag.id, prev ? { ...prev, count: prev.count + tag.count } : { ...tag });
	}
	return [...merged.values()]
		.map((tag) => {
			const count = Math.min(tag.count, total);
			return { id: tag.id, name: tag.name, count, total, label: `${count}/${total}`, onAll: count >= total };
		})
		.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

export function buildBulkTagsBody(modelIds: string[], addChips: string[], removeIds: string[], rows: SelectionTagRow[]): BulkModelTagsBody {
	const removedNames = new Set(rows.filter((row) => removeIds.includes(row.id)).map((row) => norm(row.name)));
	return {
		model_ids: [...new Set(modelIds)],
		add: addChips.map((chip) => chip.trim()).filter((chip) => chip && !removedNames.has(norm(chip))),
		remove: removeIds.filter((id) => rows.some((row) => row.id === id))
	};
}

export function hasBulkChanges(body: BulkModelTagsBody): boolean {
	return body.model_ids.length > 0 && (body.add.length > 0 || body.remove.length > 0);
}

export function pendingSummary(body: BulkModelTagsBody): string {
	const parts: string[] = [];
	if (body.add.length) parts.push(`+${body.add.length} to add`);
	if (body.remove.length) parts.push(`−${body.remove.length} to remove`);
	return parts.join(' · ');
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

export function resultSummary(result: BulkModelTagsResult): string {
	const added = result.tags.reduce((sum, tag) => sum + tag.added, 0);
	const removed = result.tags.reduce((sum, tag) => sum + tag.removed, 0);
	const parts: string[] = [];
	if (added) parts.push(`added ${plural(added, 'tag')}`);
	if (removed) parts.push(`removed ${plural(removed, 'tag')}`);
	const head = parts.length ? parts.join(', ') : 'no changes';
	const text = `${head.charAt(0).toUpperCase()}${head.slice(1)} across ${plural(result.models, 'model')}`;
	return result.unknown_model_ids.length ? `${text} · ${plural(result.unknown_model_ids.length, 'model')} not found` : text;
}
