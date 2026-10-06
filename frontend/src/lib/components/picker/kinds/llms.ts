import type { LLMConfig } from '$lib/types/llm';
import type { EntityKind, PickerBadge } from '../types';
import { byName, hostOf, uniqueOptions } from './shared';

function contextLength(config: LLMConfig): number | null {
	const options = (config.provider_options ?? {}) as Record<string, unknown>;
	const raw = options.num_ctx ?? options.context_length ?? options.n_ctx;
	return typeof raw === 'number' && raw > 0 ? raw : null;
}

function compactNumber(value: number | null | undefined): string {
	if (!value) return '';
	if (value >= 1000) return `${Math.round(value / 100) / 10}k`.replace('.0k', 'k');
	return String(value);
}

export const llmsKind: EntityKind<LLMConfig> = {
	id: 'llms',
	singular: 'LLM',
	plural: 'LLMs',
	icon: 'model',
	getId: (config) => config.id,
	getName: (config) => config.name,
	searchText: (config) => [config.name, config.id, config.type, config.model, hostOf(config.base_url)].filter(Boolean).join(' '),
	lead: () => ({ type: 'icon', name: 'model' }),
	badges: (config) => {
		const badges: PickerBadge[] = [];
		if (config.is_default) badges.push({ label: 'default', tone: 'signal' });
		if (!config.enabled) badges.push({ label: 'disabled', tone: 'warning' });
		return badges;
	},
	facts: [
		{ key: 'provider', label: 'Provider', value: (c) => c.type, as: 'tag', base: true },
		{ key: 'model', label: 'Model', value: (c) => c.model, as: 'text', base: true },
		{ key: 'endpoint', label: 'Endpoint', value: (c) => hostOf(c.base_url), as: 'text' }
	],
	columns: [
		{ key: 'context', label: 'Context', width: '80px', priority: 1, mono: true, value: (c) => compactNumber(contextLength(c)) },
		{ key: 'max_out', label: 'Max out', width: '80px', priority: 1, mono: true, value: (c) => compactNumber(c.max_tokens) },
		{ key: 'vision', label: 'Vision', width: '70px', mono: true, value: (c) => (c.supports_vision ? 'yes' : '') }
	],
	filters: [
		{
			key: 'provider',
			label: 'Provider',
			options: (rows) => uniqueOptions(rows.map((c) => c.type), 'Any provider'),
			match: (c, value) => c.type === value
		},
		{
			key: 'vision',
			label: 'Vision',
			options: () => [
				{ value: '', label: 'Any' },
				{ value: 'yes', label: 'Vision capable' }
			],
			match: (c, value) => value !== 'yes' || !!c.supports_vision
		},
		{
			key: 'enabled',
			label: 'Status',
			options: () => [
				{ value: '', label: 'Any' },
				{ value: 'enabled', label: 'Enabled' },
				{ value: 'disabled', label: 'Disabled' }
			],
			match: (c, value) => (value === 'enabled' ? c.enabled : !c.enabled)
		}
	],
	sorts: [{ value: 'name', label: 'Name', compare: byName((c: LLMConfig) => c.name) }],
	defaultSort: 'name',
	searchPlaceholder: 'Search name, provider, model',
	empty: {
		title: 'No LLM configurations',
		description: 'Add one in Admin, LLM / Assistant first, then assign it here.'
	},
	rowHeight: 58
};
