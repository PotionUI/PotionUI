export function tabChangedBeyondGeneration(previous: object, next: object): boolean {
	if (previous === next) return false;
	const left = previous as Record<string, unknown>;
	const right = next as Record<string, unknown>;
	const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
	for (const key of keys) {
		if (key === 'generation') continue;
		if (left[key] !== right[key]) return true;
	}
	return false;
}
