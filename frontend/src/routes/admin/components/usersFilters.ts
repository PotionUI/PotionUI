import type { User } from '$lib/stores/auth';
import { parseServerDate } from '$lib/utils/relativeTime';
import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip, SortOption } from '$lib/components/library/librarySection';

export type UserAccountTypeFilter = 'all' | 'USER' | 'ADMIN';
export type UserSortBy = 'username' | 'created';

export interface UsersFilters {
	q: string;
	accountType: UserAccountTypeFilter;
	sortBy: UserSortBy;
}

export const DEFAULT_USERS_FILTERS: UsersFilters = {
	q: '',
	accountType: 'all',
	sortBy: 'username'
};

export const USER_ACCOUNT_TYPE_OPTIONS: ReadonlyArray<{ value: UserAccountTypeFilter; label: string }> = [
	{ value: 'all', label: 'All Users' },
	{ value: 'USER', label: 'Regular Users' },
	{ value: 'ADMIN', label: 'Administrators' }
];

export const USERS_SORT_OPTIONS: readonly SortOption<UserSortBy>[] = [
	{ value: 'username', label: 'Username' },
	{ value: 'created', label: 'Recently added' }
];

const FIELDS: readonly FilterFieldDescriptor<UsersFilters>[] = [
	{
		kind: 'enum',
		key: 'accountType',
		param: 'account_type',
		label: 'Account type',
		values: ['USER', 'ADMIN'],
		default: 'all',
		chipLabel: (value) => (value === 'ADMIN' ? 'Administrators' : 'Regular Users')
	}
];

const codec = createFilterCodec<UsersFilters>({
	defaults: DEFAULT_USERS_FILTERS,
	fields: FIELDS,
	sortValues: ['username', 'created']
});

export function usersFiltersFromSearchParams(params: URLSearchParams): UsersFilters {
	return codec.fromSearchParams(params);
}

export function usersFiltersToSearchParams(filters: UsersFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}

export function usersFilterChips(filters: UsersFilters): FilterChip[] {
	return codec.chips(filters);
}

export function clearUsersFilterChip(filters: UsersFilters, key: string): UsersFilters {
	return codec.clearChip(filters, key);
}

export function clearAllUsersFilters(filters: UsersFilters): UsersFilters {
	return codec.clearAll(filters);
}

export function usersFilterActiveCount(filters: UsersFilters): number {
	return codec.activeCount(filters);
}

function userTimestamp(user: User): number {
	return parseServerDate(user.created_at)?.getTime() ?? 0;
}

export function applyUsersFilters(users: readonly User[], filters: UsersFilters): User[] {
	const query = filters.q.trim().toLowerCase();
	const rows = users.filter((user) => {
		if (filters.accountType !== 'all' && user.account_type !== filters.accountType) return false;
		if (!query) return true;
		return user.username.toLowerCase().includes(query) || user.email.toLowerCase().includes(query);
	});
	if (filters.sortBy === 'created') return rows.sort((a, b) => userTimestamp(b) - userTimestamp(a));
	return rows.sort((a, b) => a.username.localeCompare(b.username));
}
