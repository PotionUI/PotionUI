export interface UserPickerUser {
	id: string;
	username: string;
	email?: string;
}

export type UserPickerValue = string[] | 'all';

export function filterUsers(users: UserPickerUser[], query: string): UserPickerUser[] {
	const q = query.trim().toLowerCase();
	if (!q) return users;
	return users.filter(
		(u) => u.username?.toLowerCase().includes(q) || u.email?.toLowerCase().includes(q)
	);
}

export function isAllMode(value: UserPickerValue): boolean {
	return value === 'all';
}

export function isUserSelected(value: UserPickerValue, userId: string): boolean {
	return Array.isArray(value) && value.includes(userId);
}

export function toggleUserSelection(value: UserPickerValue, userId: string): UserPickerValue {
	const current = Array.isArray(value) ? value : [];
	return current.includes(userId) ? current.filter((id) => id !== userId) : [...current, userId];
}

export function clearSelection(): UserPickerValue {
	return [];
}

export function setAllMode(enabled: boolean): UserPickerValue {
	return enabled ? 'all' : [];
}

export function selectedCount(value: UserPickerValue, totalUsers: number): number {
	if (value === 'all') return totalUsers;
	return Array.isArray(value) ? value.length : 0;
}

export function moveActiveIndex(current: number, delta: number, length: number): number {
	if (length <= 0) return -1;
	return ((current + delta) % length + length) % length;
}
