import type { CloudCatalogItem } from '$lib/services/admin-api';
import { itemStatuses, priceSummary, taskLabel, visibleTasks } from '../../../../routes/admin/components/cloudCatalog';
import type { EntityKind, PickerBadge } from '../types';
import { byName, uniqueOptions } from './shared';

export const cloudModelsKind: EntityKind<CloudCatalogItem> = {
	id: 'cloud-models',
	singular: 'cloud model',
	plural: 'cloud models',
	icon: 'database',
	getId: (item) => item.slug,
	getName: (item) => item.label,
	searchText: (item) => [item.label, item.vendor, item.provider_model_id, item.slug, ...item.tasks].filter(Boolean).join(' '),
	lead: () => ({ type: 'icon', name: 'database' }),
	badges: (item) => {
		const badges: PickerBadge[] = [];
		for (const status of itemStatuses(item)) {
			badges.push({
				label: status,
				tone: status === 'suggested' ? 'signal' : status === 'deprecated' ? 'warning' : 'danger'
			});
		}
		return badges;
	},
	facts: [
		{ key: 'vendor', label: 'Vendor', value: (i) => i.vendor, as: 'tag', base: true },
		{ key: 'model', label: 'Model id', value: (i) => i.provider_model_id, as: 'text', base: true }
	],
	columns: [
		{
			key: 'tasks',
			label: 'Tasks',
			width: '180px',
			priority: 1,
			value: (i) => {
				const { shown, hidden } = visibleTasks(i.tasks);
				return shown.map(taskLabel).join(', ') + (hidden.length > 0 ? ` +${hidden.length}` : '');
			}
		},
		{ key: 'price', label: 'Price', width: '150px', priority: 1, mono: true, value: (i) => priceSummary(i)?.text ?? '' },
		{ key: 'enabled', label: 'Enabled', width: '80px', mono: true, value: (i) => (i.enabled ? 'on' : 'off') }
	],
	filters: [
		{
			key: 'task',
			label: 'Task',
			options: (rows) => uniqueOptions(rows.flatMap((i) => i.tasks), 'Any task'),
			match: (i, value) => i.tasks.includes(value)
		},
		{
			key: 'enabled',
			label: 'State',
			options: () => [
				{ value: '', label: 'Any' },
				{ value: 'on', label: 'Enabled' },
				{ value: 'off', label: 'Not enabled' }
			],
			match: (i, value) => (value === 'on' ? i.enabled : !i.enabled)
		}
	],
	sorts: [{ value: 'name', label: 'Name', compare: byName((i: CloudCatalogItem) => i.label) }],
	defaultSort: 'name',
	searchPlaceholder: 'Search model, vendor, task',
	empty: { title: 'No cloud models', description: 'Refresh the catalog to discover the models this provider offers.' },
	rowHeight: 58
};
