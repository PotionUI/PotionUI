export interface MarkLike {
	kind: 'stack' | 'pixels';
	command?: { bytes: number };
}

export const MARK_LIMIT = 400;

export function markBytes(mark: MarkLike): number {
	return mark.kind === 'pixels' ? (mark.command?.bytes ?? 0) : 0;
}

export function trimMarks<T extends MarkLike>(marks: T[], budgetBytes: number, limit = MARK_LIMIT): T[] {
	let start = Math.max(0, marks.length - limit);
	let total = 0;
	for (let i = start; i < marks.length; i++) total += markBytes(marks[i]);
	while (start < marks.length - 1 && total > budgetBytes) {
		total -= markBytes(marks[start]);
		start += 1;
	}
	return start === 0 ? marks : marks.slice(start);
}
