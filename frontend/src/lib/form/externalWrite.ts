export function isEchoOnly(
	previous: Record<string, unknown> | null,
	next: Record<string, unknown>,
	written: ReadonlySet<string>
): boolean {
	if (!previous) return false;
	const keys = new Set([...Object.keys(previous), ...Object.keys(next)]);
	for (const key of keys) {
		if (written.has(key)) continue;
		if (JSON.stringify(previous[key]) !== JSON.stringify(next[key])) return false;
	}
	return true;
}
