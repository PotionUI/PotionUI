import type { UserGroup } from '$lib/services/admin-api';
import type { EntityKind, PickerBadge } from '../types';
import { byName } from './shared';

function count(value: number | undefined): string {
	return value === undefined ? '' : String(value);
}

export const groupsKind: EntityKind<UserGroup> = {
	id: 'groups',
	singular: 'group',
	plural: 'groups',
	icon: 'group',
	getId: (group) => group.id,
	getName: (group) => group.name,
	searchText: (group) => [group.name, group.description, group.id].filter(Boolean).join(' '),
	lead: () => ({ type: 'icon', name: 'group' }),
	badges: (group) => {
		const badges: PickerBadge[] = [];
		if (group.is_system) badges.push({ label: 'system', tone: 'neutral' });
		return badges;
	},
	facts: [{ key: 'description', label: 'Description', value: (g) => g.description, as: 'text', base: true }],
	columns: [
		{ key: 'members', label: 'Members', width: '80px', mono: true, value: (g) => count(g.member_count) },
		{ key: 'presets', label: 'Presets', width: '70px', priority: 1, mono: true, value: (g) => count(g.preset_count) },
		{ key: 'llms', label: 'LLMs', width: '60px', priority: 1, mono: true, value: (g) => count(g.llm_count) },
		{ key: 'models', label: 'Models', width: '70px', priority: 1, mono: true, value: (g) => count(g.model_count) }
	],
	filters: [
		{
			key: 'system',
			label: 'Type',
			options: () => [
				{ value: '', label: 'Any group' },
				{ value: 'custom', label: 'Custom groups' },
				{ value: 'system', label: 'System groups' }
			],
			match: (g, value) => (value === 'system' ? !!g.is_system : !g.is_system)
		}
	],
	sorts: [{ value: 'name', label: 'Name', compare: byName((g: UserGroup) => g.name) }],
	defaultSort: 'name',
	searchPlaceholder: 'Search name, description',
	empty: { title: 'No groups yet', description: 'Create a group under Users, Groups first.' },
	rowHeight: 52
};
