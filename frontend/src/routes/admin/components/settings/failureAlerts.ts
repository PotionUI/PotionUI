export const FAILURE_ALERT_CATEGORIES: { value: string; label: string }[] = [
	{ value: 'cuda_oom', label: 'GPU out of memory' },
	{ value: 'host_ram_oom', label: 'Host RAM exhausted' },
	{ value: 'missing_model_file', label: 'Missing model file' },
	{ value: 'disk_full', label: 'Disk full' },
	{ value: 'corrupt_weights', label: 'Corrupt weights' },
	{ value: 'auth_required', label: 'Credentials required' },
	{ value: 'backend_unreachable', label: 'Backend unreachable' },
	{ value: 'unclassified', label: 'Unclassified' }
];

export function normalizeCategories(value: unknown): string[] {
	if (Array.isArray(value)) return value.filter((item): item is string => typeof item === 'string');
	if (typeof value === 'string' && value.trim()) {
		try {
			return normalizeCategories(JSON.parse(value));
		} catch {
			return [];
		}
	}
	return [];
}

export function toggleCategory(current: unknown, category: string): string[] {
	const selected = normalizeCategories(current);
	return selected.includes(category)
		? selected.filter((item) => item !== category)
		: [...selected, category];
}
