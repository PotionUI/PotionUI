function isPlain(value: object): boolean {
	const proto = Object.getPrototypeOf(value);
	return proto === Object.prototype || proto === null || Array.isArray(value);
}

export function deepEqual(a: unknown, b: unknown): boolean {
	if (a === b) return true;
	if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false;
	if (!isPlain(a) || !isPlain(b)) return false;
	if (Array.isArray(a) !== Array.isArray(b)) return false;
	if (Array.isArray(a)) {
		const other = b as unknown[];
		if (a.length !== other.length) return false;
		for (let i = 0; i < a.length; i++) if (!deepEqual(a[i], other[i])) return false;
		return true;
	}
	const left = a as Record<string, unknown>;
	const right = b as Record<string, unknown>;
	const keys = Object.keys(left);
	if (keys.length !== Object.keys(right).length) return false;
	for (const key of keys) {
		if (!Object.prototype.hasOwnProperty.call(right, key) || !deepEqual(left[key], right[key])) return false;
	}
	return true;
}

export function createDataSharer<T>() {
	const previous = new Map<string, T>();
	return {
		share(key: string, next: T): T {
			const prior = previous.get(key);
			if (prior !== undefined && deepEqual(prior, next)) return prior;
			previous.set(key, next);
			return next;
		}
	};
}
