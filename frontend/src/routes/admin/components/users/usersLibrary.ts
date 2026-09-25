import type { LibrarySectionMeta } from '$lib/components/library/librarySection';
import type { User } from '$lib/stores/auth';
import type { UserGroup } from '$lib/services/admin-api';

export type UsersSubView = 'users' | 'groups';

export const USERS_LIBRARY_SECTIONS: readonly LibrarySectionMeta<UsersSubView>[] = [
	{ id: 'users', label: 'All users', icon: 'user' },
	{ id: 'groups', label: 'Groups', icon: 'group' }
];

export interface AccountTypeCounts {
	ADMIN: number;
	USER: number;
}

export function accountTypeCounts(users: readonly User[]): AccountTypeCounts {
	return users.reduce<AccountTypeCounts>(
		(counts, user) => {
			if (user.account_type === 'ADMIN') counts.ADMIN += 1;
			else counts.USER += 1;
			return counts;
		},
		{ ADMIN: 0, USER: 0 }
	);
}

export interface DeletableGroupsResult {
	deletableIds: string[];
	skippedCount: number;
}

export function deletableGroupIds(groups: readonly UserGroup[], selectedIds: ReadonlySet<string>): DeletableGroupsResult {
	const selected = groups.filter((group) => selectedIds.has(group.id));
	const deletableIds = selected.filter((group) => !group.is_system).map((group) => group.id);
	return { deletableIds, skippedCount: selected.length - deletableIds.length };
}

export function deletableUserIds(selectedIds: ReadonlySet<string>, currentUserId: string | null | undefined): DeletableGroupsResult {
	const ids = Array.from(selectedIds);
	const deletableIds = ids.filter((id) => id !== currentUserId);
	return { deletableIds, skippedCount: ids.length - deletableIds.length };
}
