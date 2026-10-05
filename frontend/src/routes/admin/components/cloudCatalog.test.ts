import { describe, it, expect } from 'vitest';
import type { CloudCatalogItem } from '$lib/services/admin-api';
import {
	DEFAULT_CATALOG_FILTERS,
	bulkTargets,
	canEnable,
	catalogFilterActiveCount,
	catalogFilterChips,
	catalogFiltersFromSearchParams,
	catalogFiltersToSearchParams,
	catalogHasFilters,
	catalogQuery,
	catalogSummaryLine,
	clearAllCatalogFilters,
	clearCatalogFilterChip,
	formatPriceLine,
	formatUsd,
	itemStatuses,
	outputLabel,
	priceSummary,
	taskLabel,
	visibleTasks,
	catalogPresetsHint,
	catalogShowOf,
	modelPageHref,
	suggestedTip,
	withCatalogShow
} from './cloudCatalog';

function item(overrides: Partial<CloudCatalogItem> = {}): CloudCatalogItem {
	return {
		slug: 'm',
		provider_model_id: 'vendor/m',
		label: 'M',
		vendor: null,
		description: null,
		tasks: ['txt2img'],
		outputs: ['image'],
		enabled: false,
		suggested: false,
		available: true,
		missing_since: null,
		deprecated: false,
		deprecated_at: null,
		discovered_at: null,
		refreshed_at: null,
		enabled_at: null,
		model_id: null,
		max_outputs_per_job: 1,
		typical_seconds: null,
		max_seconds: null,
		params: [],
		inputs: [],
		pricing: [],
		...overrides
	};
}

describe('taskLabel', () => {
	it('uses plain words for known tasks', () => {
		expect(taskLabel('txt2img')).toBe('Text to image');
		expect(taskLabel('img_edit')).toBe('Edit');
		expect(taskLabel('txt2video')).toBe('Text to video');
		expect(taskLabel('img2video')).toBe('Image to video');
	});

	it('humanises a task it does not know', () => {
		expect(taskLabel('depth_map')).toBe('Depth map');
	});

	it('labels outputs', () => {
		expect(outputLabel('video')).toBe('Video');
		expect(outputLabel('image')).toBe('Images');
	});
});

describe('formatUsd', () => {
	it('keeps two decimals for whole cents', () => {
		expect(formatUsd('0.04')).toBe('$0.04');
		expect(formatUsd('0.4')).toBe('$0.40');
		expect(formatUsd('2')).toBe('$2.00');
	});

	it('keeps fractions of a cent', () => {
		expect(formatUsd('0.0005')).toBe('$0.0005');
		expect(formatUsd('0.040000')).toBe('$0.04');
	});

	it('rejects values it cannot show', () => {
		expect(formatUsd('abc')).toBeNull();
		expect(formatUsd('-1')).toBeNull();
	});
});

describe('formatPriceLine', () => {
	it('puts the unit after a slash', () => {
		expect(formatPriceLine({ unit: 'image', usd: '0.04', applies_to: null })).toBe('$0.04 / image');
		expect(formatPriceLine({ unit: 'second', usd: '0.40', applies_to: null })).toBe('$0.40 / s');
		expect(formatPriceLine({ unit: 'megapixel', usd: '0.01', applies_to: null })).toBe('$0.01 / MP');
		expect(formatPriceLine({ unit: 'request', usd: '0.1', applies_to: null })).toBe('$0.10 / request');
	});

	it('shows token prices per million tokens', () => {
		expect(formatPriceLine({ unit: 'token', usd: '0.0000003', applies_to: null })).toBe('$0.30 / 1M tokens');
	});

	it('appends what the price applies to', () => {
		expect(formatPriceLine({ unit: 'second', usd: '0.4', applies_to: '1080p' })).toBe('$0.40 / s · 1080p');
	});

	it('drops a line whose amount is unreadable', () => {
		expect(formatPriceLine({ unit: 'image', usd: 'free', applies_to: null })).toBeNull();
	});
});

describe('priceSummary', () => {
	it('is null without pricing', () => {
		expect(priceSummary(item())).toBeNull();
	});

	it('shows one line and counts the rest', () => {
		const summary = priceSummary(
			item({
				pricing: [
					{ unit: 'second', usd: '0.40', applies_to: '1080p' },
					{ unit: 'second', usd: '0.20', applies_to: '720p' },
					{ unit: 'request', usd: '0.01', applies_to: null }
				]
			})
		);
		expect(summary?.text).toBe('$0.40 / s · 1080p');
		expect(summary?.extra).toBe(2);
		expect(summary?.full).toBe('$0.40 / s · 1080p; $0.20 / s · 720p; $0.01 / request');
	});
});

describe('visibleTasks', () => {
	it('caps the badges and keeps the rest for a tooltip', () => {
		expect(visibleTasks(['a', 'b', 'c', 'd'])).toEqual({ shown: ['a', 'b'], hidden: ['c', 'd'] });
		expect(visibleTasks(['a'])).toEqual({ shown: ['a'], hidden: [] });
	});
});

describe('itemStatuses', () => {
	it('flags missing and deprecated models', () => {
		expect(itemStatuses(item({ available: false, deprecated: true }))).toEqual(['missing', 'deprecated']);
		expect(itemStatuses(item({ deprecated: true }))).toEqual(['deprecated']);
	});

	it('only suggests a model that is usable', () => {
		expect(itemStatuses(item({ suggested: true }))).toEqual(['suggested']);
		expect(itemStatuses(item({ suggested: true, deprecated: true }))).toEqual(['deprecated']);
		expect(itemStatuses(item({ suggested: true, available: false }))).toEqual(['missing']);
	});

	it('has no status for a plain model', () => {
		expect(itemStatuses(item())).toEqual([]);
	});
});

describe('canEnable', () => {
	it('blocks turning on a model the provider no longer lists', () => {
		expect(canEnable(item({ available: false }))).toBe(false);
		expect(canEnable(item({ available: false, enabled: true }))).toBe(true);
		expect(canEnable(item())).toBe(true);
	});
});

describe('bulkTargets', () => {
	const items = [
		item({ slug: 'a', enabled: false }),
		item({ slug: 'b', enabled: true }),
		item({ slug: 'c', enabled: false, available: false }),
		item({ slug: 'd', enabled: false })
	];

	it('enables only selected models that are off and available', () => {
		expect(bulkTargets(items, new Set(['a', 'b', 'c']), true)).toEqual(['a']);
	});

	it('disables only selected models that are on', () => {
		expect(bulkTargets(items, new Set(['a', 'b']), false)).toEqual(['b']);
	});

	it('ignores slugs that are not on the page', () => {
		expect(bulkTargets(items, new Set(['zzz']), true)).toEqual([]);
	});
});

describe('catalog filters', () => {
	it('round-trips through the URL', () => {
		const filters = { ...DEFAULT_CATALOG_FILTERS, q: 'veo', task: 'txt2video', output: 'video', enabledOnly: true };
		const params = catalogFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('veo');
		expect(params.get('task')).toBe('txt2video');
		expect(params.get('output')).toBe('video');
		expect(params.get('enabled')).toBe('1');
		expect(catalogFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('writes nothing for the defaults', () => {
		expect(catalogFiltersToSearchParams(DEFAULT_CATALOG_FILTERS).toString()).toBe('');
	});

	it('ignores a task it does not know', () => {
		expect(catalogFiltersFromSearchParams(new URLSearchParams('task=bogus')).task).toBe('');
	});

	it('counts filters but not the search text', () => {
		const filters = { ...DEFAULT_CATALOG_FILTERS, q: 'x', task: 'txt2img', enabledOnly: true };
		expect(catalogFilterActiveCount(filters)).toBe(2);
		expect(catalogHasFilters({ ...DEFAULT_CATALOG_FILTERS, q: 'x' })).toBe(true);
		expect(catalogHasFilters(DEFAULT_CATALOG_FILTERS)).toBe(false);
	});

	it('words its chips in plain language', () => {
		const chips = catalogFilterChips({ ...DEFAULT_CATALOG_FILTERS, task: 'img2video', enabledOnly: true });
		expect(chips.map((chip) => chip.label)).toEqual(['Image to video', 'Enabled only']);
	});

	it('clears one chip or all of them', () => {
		const filters = { ...DEFAULT_CATALOG_FILTERS, q: 'x', task: 'txt2img', enabledOnly: true };
		expect(clearCatalogFilterChip(filters, 'task').task).toBe('');
		expect(clearCatalogFilterChip(filters, 'enabledOnly').enabledOnly).toBe(false);
		const cleared = clearAllCatalogFilters(filters);
		expect(cleared.task).toBe('');
		expect(cleared.enabledOnly).toBe(false);
		expect(cleared.q).toBe('x');
	});
});

describe('catalogQuery', () => {
	it('sends paging and only the filters that are set', () => {
		expect(catalogQuery(DEFAULT_CATALOG_FILTERS, 1, 50)).toEqual({ limit: 50, offset: 0 });
		expect(catalogQuery(DEFAULT_CATALOG_FILTERS, 3, 50)).toEqual({ limit: 50, offset: 100 });
		expect(
			catalogQuery({ ...DEFAULT_CATALOG_FILTERS, q: ' veo ', task: 'txt2video', output: 'video', enabledOnly: true }, 2, 25)
		).toEqual({ limit: 25, offset: 25, task: 'txt2video', output: 'video', enabled: true, search: 'veo' });
	});
});

describe('catalogSummaryLine', () => {
	it('counts models and enabled models', () => {
		expect(catalogSummaryLine({ total: 412, enabled: 6 })).toBe('412 models · 6 enabled');
		expect(catalogSummaryLine({ total: 1, enabled: 0 })).toBe('1 model · 0 enabled');
	});
});

describe('suggested models', () => {
	it('can be the only models shown, apart from enabled only', () => {
		const suggested = withCatalogShow({ ...DEFAULT_CATALOG_FILTERS, enabledOnly: true }, 'suggested');
		expect(suggested.suggestedOnly).toBe(true);
		expect(suggested.enabledOnly).toBe(false);
		expect(catalogShowOf(suggested)).toBe('suggested');
		expect(catalogShowOf(withCatalogShow(suggested, 'enabled'))).toBe('enabled');
		expect(catalogShowOf(withCatalogShow(suggested, 'all'))).toBe('all');
		expect(catalogQuery(suggested, 1, 50)).toEqual({ limit: 50, offset: 0, suggested: true });
	});

	it('round-trips through the URL and shows as a removable chip', () => {
		const filters = { ...DEFAULT_CATALOG_FILTERS, suggestedOnly: true };
		const params = catalogFiltersToSearchParams(filters);
		expect(params.get('suggested')).toBeTruthy();
		expect(catalogFiltersFromSearchParams(params).suggestedOnly).toBe(true);
		expect(catalogFilterChips(filters).map((chip) => chip.label)).toContain('Suggested');
		expect(clearCatalogFilterChip(filters, 'suggestedOnly').suggestedOnly).toBe(false);
	});

	it('credit the plugin that suggests them', () => {
		expect(suggestedTip('OpenRouter')).toBe('Recommended by the OpenRouter plugin.');
		expect(suggestedTip(null)).toBe('Recommended by the plugin that adds this backend.');
		expect(suggestedTip('  ')).toBe('Recommended by the plugin that adds this backend.');
	});
});

describe('modelPageHref', () => {
	it('links an enabled model to its model page', () => {
		expect(modelPageHref(item({ enabled: true, model_id: 'abc' }))).toBe('/admin?tab=models&id=abc');
	});

	it('has no link before the model is enabled', () => {
		expect(modelPageHref(item({ enabled: false, model_id: 'abc' }))).toBeNull();
		expect(modelPageHref(item({ enabled: true, model_id: null }))).toBeNull();
	});
});

describe('catalogPresetsHint', () => {
	const preset = (id: string, name: string, extra: Record<string, unknown> = {}) => ({ id, name, driver: 'cloud.fake', ...extra });

	it('says nothing until a model is enabled', () => {
		expect(catalogPresetsHint([preset('p1', 'Fake Image')], 'cloud.fake', 0)).toBeNull();
		expect(catalogPresetsHint(null, 'cloud.fake', 2)).toBeNull();
	});

	it('points to the first preset of this backend when none is installed', () => {
		const hint = catalogPresetsHint(
			[preset('p2', 'Fake Video'), preset('p1', 'Fake Image'), { id: 'other', name: 'Another', driver: 'cloud.other' }],
			'cloud.fake',
			1
		);
		expect(hint?.href).toBe('/admin?tab=presets&id=p1');
		expect(hint?.description).toContain('2 presets use this backend');
	});

	it('asks for an assignment once a preset is installed', () => {
		const hint = catalogPresetsHint([preset('p1', 'Fake Image'), preset('p2', 'Fake Video', { installed: true })], 'cloud.fake', 1);
		expect(hint?.href).toBe('/admin?tab=presets&id=p2');
		expect(hint?.description).toBe('Assign Fake Video to users or groups so it shows on Generate.');
	});

	it('goes away once an installed preset is assigned to a user or a group', () => {
		expect(catalogPresetsHint([preset('p1', 'Fake Image', { installed: true, assignment_count: 1 })], 'cloud.fake', 1)).toBeNull();
		expect(catalogPresetsHint([preset('p1', 'Fake Image', { installed: true, group_count: 1 })], 'cloud.fake', 1)).toBeNull();
	});

	it('says nothing when no preset uses this backend', () => {
		expect(catalogPresetsHint([{ id: 'o', name: 'Other', driver: 'cloud.other' }], 'cloud.fake', 1)).toBeNull();
	});
});
