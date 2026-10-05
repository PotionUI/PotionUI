export const SHARED_BACKEND_FIELDS = [
	'name',
	'enabled',
	'priority',
	'scheduling_policy',
	'scheduling_max_consecutive_same_model'
] as const;

export function carrySharedBackendFields<T extends Record<string, unknown>>(previous: Record<string, unknown>, next: T): T {
	const carried: Record<string, unknown> = { ...next };
	for (const key of SHARED_BACKEND_FIELDS) {
		if (previous[key] !== undefined) carried[key] = previous[key];
	}
	return carried as T;
}
