export interface LibrarySectionMeta<S extends string = string> {
	id: S;
	label: string;
	icon: string;
}

export interface SortOption<T extends string = string> {
	value: T;
	label: string;
}

export interface FilterChip {
	key: string;
	label: string;
}

export function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
	return value && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}
