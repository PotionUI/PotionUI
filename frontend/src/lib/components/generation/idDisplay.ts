export const FULL_ID_MAX = 26;

export function middleEllipsis(id: string, max: number = FULL_ID_MAX): string {
	if (id.length <= max) return id;
	const keep = max - 1;
	const head = Math.ceil(keep / 2);
	const tail = keep - head;
	return `${id.slice(0, head)}…${id.slice(id.length - tail)}`;
}
