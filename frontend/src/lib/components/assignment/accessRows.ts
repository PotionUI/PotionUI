/**
 * Users and groups are two different grants of the same thing, so the access
 * pane renders one row shape for both. Assigned entries sort to the top: the
 * pane's whole job is answering "who can see this resource", and that answer
 * has to be readable without scrolling past everyone who can't.
 */
export interface AccessRow {
	id: string;
	/** Matches the pending-toggle key the pane tracks per in-flight request. */
	key: string;
	title: string;
	subtitle: string;
	badge: { variant: 'warning' | 'neutral'; text: string } | null;
	assigned: boolean;
}

export interface AccessUser {
	id: string;
	username: string;
	email?: string | null;
	account_type?: string | null;
}

export interface AccessGroup {
	id: string;
	name: string;
	description?: string | null;
	member_count?: number | null;
}

function normalize(query: string): string {
	return query.trim().toLowerCase();
}

export function toUserAccessRows(
	users: AccessUser[],
	assignedIds: Set<string>,
	query: string
): AccessRow[] {
	const q = normalize(query);
	return users
		.filter(
			(user) =>
				!q ||
				user.username.toLowerCase().includes(q) ||
				user.email?.toLowerCase().includes(q)
		)
		.sort(
			(a, b) =>
				Number(assignedIds.has(b.id)) - Number(assignedIds.has(a.id)) ||
				a.username.localeCompare(b.username)
		)
		.map((user) => ({
			id: user.id,
			key: `user:${user.id}`,
			title: user.username,
			subtitle: user.email || '',
			badge: user.account_type === 'ADMIN' ? { variant: 'warning' as const, text: 'admin' } : null,
			assigned: assignedIds.has(user.id)
		}));
}

export function toGroupAccessRows(
	groups: AccessGroup[],
	assignedIds: Set<string>,
	query: string
): AccessRow[] {
	const q = normalize(query);
	return groups
		.filter(
			(group) =>
				!q ||
				group.name.toLowerCase().includes(q) ||
				group.description?.toLowerCase().includes(q)
		)
		.sort(
			(a, b) =>
				Number(assignedIds.has(b.id)) - Number(assignedIds.has(a.id)) ||
				a.name.localeCompare(b.name)
		)
		.map((group) => ({
			id: group.id,
			key: `group:${group.id}`,
			title: group.name,
			subtitle: group.description || '',
			badge:
				group.member_count != null
					? { variant: 'neutral' as const, text: `${group.member_count} members` }
					: null,
			assigned: assignedIds.has(group.id)
		}));
}
