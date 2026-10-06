import type { User } from '$lib/stores/auth';
import { timeAgo } from '$lib/utils/relativeTime';
import type { EntityKind, PickerBadge } from '../types';
import { byName, initials, uniqueOptions } from './shared';

export interface PickerUser extends User {
	groups?: string[];
	plan?: string | null;
	planSource?: string | null;
}

export const usersKind: EntityKind<PickerUser> = {
	id: 'users',
	singular: 'user',
	plural: 'users',
	icon: 'user',
	getId: (user) => user.id,
	getName: (user) => user.username,
	searchText: (user) => [user.username, user.email, user.id, ...(user.groups ?? [])].filter(Boolean).join(' '),
	lead: (user) => ({ type: 'avatar', text: initials(user.username), src: user.avatar_url }),
	badges: (user) => {
		const badges: PickerBadge[] = [];
		if (user.account_type === 'ADMIN') badges.push({ label: 'admin', tone: 'warning' });
		return badges;
	},
	facts: [
		{ key: 'email', label: 'Email', value: (u) => u.email, as: 'text', base: true },
		{ key: 'last_login', label: 'Last login', value: (u) => (u.last_login ? timeAgo(u.last_login) : ''), as: 'text' }
	],
	columns: [
		{ key: 'groups', label: 'Groups', width: '70px', priority: 1, mono: true, value: (u) => (u.groups ? String(u.groups.length) : '') },
		{
			key: 'plan',
			label: 'Plan',
			width: '150px',
			priority: 1,
			value: (u) => (u.plan ? `${u.plan}${u.planSource ? ` (${u.planSource})` : ''}` : '')
		},
		{ key: 'last_login', label: 'Last login', width: '100px', priority: 2, mono: true, value: (u) => (u.last_login ? timeAgo(u.last_login) : 'never') }
	],
	filters: [
		{
			key: 'account_type',
			label: 'Account',
			options: () => [
				{ value: '', label: 'Any account' },
				{ value: 'USER', label: 'Users' },
				{ value: 'ADMIN', label: 'Admins' }
			],
			match: (u, value) => u.account_type === value
		},
		{
			key: 'group',
			label: 'Group',
			options: (rows) => uniqueOptions(rows.flatMap((u) => u.groups ?? []), 'Any group'),
			match: (u, value) => (u.groups ?? []).includes(value)
		},
		{
			key: 'plan',
			label: 'Plan',
			options: (rows) => uniqueOptions(rows.map((u) => u.plan), 'Any plan'),
			match: (u, value) => u.plan === value
		}
	],
	sorts: [
		{ value: 'name', label: 'Name', compare: byName((u: PickerUser) => u.username) },
		{
			value: 'login',
			label: 'Last login',
			compare: (a, b) => (b.last_login ?? '').localeCompare(a.last_login ?? '')
		}
	],
	defaultSort: 'name',
	searchPlaceholder: 'Search username, email',
	empty: { title: 'No users yet', description: 'Accounts show up here once people sign up or you add them.' },
	rowHeight: 52
};
