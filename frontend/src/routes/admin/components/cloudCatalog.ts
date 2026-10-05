import { createFilterCodec, type FilterFieldDescriptor } from '$lib/components/library/filterCodec';
import type { FilterChip } from '$lib/components/library/librarySection';
import type { CloudCatalogItem, CloudCatalogQuery, CloudPriceLine } from '$lib/services/admin-api';
import type { PresetInfo } from '$lib/types/api';

export const CATALOG_PAGE_SIZE = 50;
export const CATALOG_PAGE_SIZE_OPTIONS = [25, 50, 100, 200] as const;
export const MAX_VISIBLE_TASKS = 2;

export const CATALOG_FILTER_PARAMS = ['q', 'task', 'output', 'enabled', 'suggested'] as const;

const TASK_LABELS: Record<string, string> = {
	txt2img: 'Text to image',
	img_edit: 'Edit',
	inpaint: 'Inpaint',
	txt2video: 'Text to video',
	img2video: 'Image to video',
	ref2video: 'Reference to video',
	video2video: 'Video to video',
	txt2audio: 'Text to audio',
	txt2speech: 'Text to speech',
	upscale_image: 'Upscale image',
	upscale_video: 'Upscale video'
};

const OUTPUT_LABELS: Record<string, string> = {
	image: 'Images',
	video: 'Video',
	audio: 'Audio'
};

export function taskLabel(task: string): string {
	const known = TASK_LABELS[task];
	if (known) return known;
	const words = task.replace(/[_-]+/g, ' ').trim();
	return words ? words.charAt(0).toUpperCase() + words.slice(1) : task;
}

export function outputLabel(output: string): string {
	return OUTPUT_LABELS[output] ?? taskLabel(output);
}

export const CATALOG_TASK_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
	{ value: '', label: 'All tasks' },
	...Object.entries(TASK_LABELS).map(([value, label]) => ({ value, label }))
];

export const CATALOG_OUTPUT_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
	{ value: '', label: 'All outputs' },
	...Object.entries(OUTPUT_LABELS).map(([value, label]) => ({ value, label }))
];

export type CatalogShow = 'all' | 'enabled' | 'suggested';

export const CATALOG_SHOW_OPTIONS: ReadonlyArray<{ value: CatalogShow; label: string }> = [
	{ value: 'all', label: 'All models' },
	{ value: 'enabled', label: 'Enabled only' },
	{ value: 'suggested', label: 'Suggested' }
];

export function catalogShowOf(filters: Pick<CatalogFilters, 'enabledOnly' | 'suggestedOnly'>): CatalogShow {
	if (filters.enabledOnly) return 'enabled';
	if (filters.suggestedOnly) return 'suggested';
	return 'all';
}

export function withCatalogShow(filters: CatalogFilters, show: CatalogShow): CatalogFilters {
	return { ...filters, enabledOnly: show === 'enabled', suggestedOnly: show === 'suggested' };
}

export function suggestedTip(providerLabel: string | null | undefined): string {
	const name = providerLabel?.trim();
	return name ? `Recommended by the ${name} plugin.` : 'Recommended by the plugin that adds this backend.';
}

export interface CatalogFilters {
	q: string;
	sortBy: 'default';
	task: string;
	output: string;
	enabledOnly: boolean;
	suggestedOnly: boolean;
}

export const DEFAULT_CATALOG_FILTERS: CatalogFilters = {
	q: '',
	sortBy: 'default',
	task: '',
	output: '',
	enabledOnly: false,
	suggestedOnly: false
};

const FIELDS: readonly FilterFieldDescriptor<CatalogFilters>[] = [
	{
		kind: 'enum',
		key: 'task',
		param: 'task',
		label: 'Task',
		values: Object.keys(TASK_LABELS),
		default: '',
		chipLabel: taskLabel
	},
	{
		kind: 'enum',
		key: 'output',
		param: 'output',
		label: 'Output',
		values: Object.keys(OUTPUT_LABELS),
		default: '',
		chipLabel: outputLabel
	},
	{ kind: 'boolean', key: 'enabledOnly', param: 'enabled', label: 'Enabled only', chipLabel: 'Enabled only' },
	{ kind: 'boolean', key: 'suggestedOnly', param: 'suggested', label: 'Suggested', chipLabel: 'Suggested' }
];

const codec = createFilterCodec<CatalogFilters>({
	defaults: DEFAULT_CATALOG_FILTERS,
	fields: FIELDS,
	sortValues: ['default']
});

export function catalogFiltersFromSearchParams(params: URLSearchParams): CatalogFilters {
	return codec.fromSearchParams(params);
}

export function catalogFiltersToSearchParams(filters: CatalogFilters): URLSearchParams {
	return codec.toSearchParams(filters);
}

export function catalogFilterActiveCount(filters: CatalogFilters): number {
	return codec.activeCount(filters);
}

export function catalogFilterChips(filters: CatalogFilters): FilterChip[] {
	return codec.chips(filters);
}

export function clearCatalogFilterChip(filters: CatalogFilters, key: string): CatalogFilters {
	return codec.clearChip(filters, key);
}

export function clearAllCatalogFilters(filters: CatalogFilters): CatalogFilters {
	return codec.clearAll(filters);
}

export function catalogHasFilters(filters: CatalogFilters): boolean {
	return catalogFilterActiveCount(filters) > 0 || filters.q.trim() !== '';
}

export function catalogQuery(filters: CatalogFilters, pageIndex: number, pageSize: number): CloudCatalogQuery {
	const query: CloudCatalogQuery = {
		limit: pageSize,
		offset: Math.max(0, (pageIndex - 1) * pageSize)
	};
	if (filters.task) query.task = filters.task;
	if (filters.output) query.output = filters.output;
	if (filters.enabledOnly) query.enabled = true;
	if (filters.suggestedOnly) query.suggested = true;
	const search = filters.q.trim();
	if (search) query.search = search;
	return query;
}

const UNIT_SUFFIX: Record<string, string> = {
	request: 'request',
	image: 'image',
	megapixel: 'MP',
	second: 's',
	sku: 'item'
};

function trimmedDecimal(value: number): string {
	const fixed = value.toFixed(6);
	const [whole, fraction = ''] = fixed.split('.');
	const trimmed = fraction.replace(/0+$/, '');
	return `${whole}.${trimmed.padEnd(2, '0')}`;
}

export function formatUsd(usd: string | number): string | null {
	const value = typeof usd === 'number' ? usd : Number(usd);
	if (!Number.isFinite(value) || value < 0) return null;
	return `$${trimmedDecimal(value)}`;
}

export function formatPriceLine(line: CloudPriceLine): string | null {
	const value = Number(line.usd);
	if (!Number.isFinite(value) || value < 0) return null;
	const qualifier = line.applies_to ? ` · ${line.applies_to}` : '';
	if (line.unit === 'token') return `$${trimmedDecimal(value * 1_000_000)} / 1M tokens${qualifier}`;
	const suffix = UNIT_SUFFIX[line.unit] ?? line.unit;
	return `$${trimmedDecimal(value)} / ${suffix}${qualifier}`;
}

export function priceLines(item: Pick<CloudCatalogItem, 'pricing'>): string[] {
	return item.pricing.map(formatPriceLine).filter((line): line is string => line !== null);
}

export interface PriceSummary {
	text: string;
	full: string;
	extra: number;
}

export function priceSummary(item: Pick<CloudCatalogItem, 'pricing'>): PriceSummary | null {
	const lines = priceLines(item);
	if (lines.length === 0) return null;
	return { text: lines[0], full: lines.join('; '), extra: lines.length - 1 };
}

export function visibleTasks(tasks: readonly string[], max: number = MAX_VISIBLE_TASKS): { shown: string[]; hidden: string[] } {
	return { shown: tasks.slice(0, max), hidden: tasks.slice(max) };
}

export type CatalogStatus = 'deprecated' | 'missing' | 'suggested';

export function itemStatuses(item: Pick<CloudCatalogItem, 'available' | 'deprecated' | 'suggested'>): CatalogStatus[] {
	const statuses: CatalogStatus[] = [];
	if (!item.available) statuses.push('missing');
	if (item.deprecated) statuses.push('deprecated');
	if (item.suggested && item.available && !item.deprecated) statuses.push('suggested');
	return statuses;
}

export function canEnable(item: Pick<CloudCatalogItem, 'available' | 'enabled'>): boolean {
	return item.enabled || item.available;
}

export function bulkTargets(items: readonly CloudCatalogItem[], selected: ReadonlySet<string>, enable: boolean): string[] {
	return items
		.filter((item) => selected.has(item.slug))
		.filter((item) => (enable ? !item.enabled && item.available : item.enabled))
		.map((item) => item.slug);
}

export function catalogSummaryLine(counts: { total: number; enabled: number }): string {
	const models = `${counts.total} ${counts.total === 1 ? 'model' : 'models'}`;
	return `${models} · ${counts.enabled} enabled`;
}

export function modelPageHref(item: Pick<CloudCatalogItem, 'enabled' | 'model_id'>): string | null {
	if (!item.enabled || !item.model_id) return null;
	return `/admin?${new URLSearchParams({ tab: 'models', id: item.model_id }).toString()}`;
}

export interface CatalogPresetsHint {
	description: string;
	href: string;
}

type HintPreset = Pick<PresetInfo, 'id' | 'name' | 'driver' | 'installed' | 'assignment_count' | 'group_count'>;

function presetHref(id: string): string {
	return `/admin?${new URLSearchParams({ tab: 'presets', id }).toString()}`;
}

export function catalogPresetsHint(
	presets: readonly HintPreset[] | null,
	driver: string | null | undefined,
	enabledCount: number
): CatalogPresetsHint | null {
	if (!presets || !driver || enabledCount <= 0) return null;
	const matching = presets.filter((preset) => preset.driver === driver).sort((a, b) => a.name.localeCompare(b.name));
	if (matching.length === 0) return null;
	const installed = matching.filter((preset) => preset.installed);
	if (installed.some((preset) => (preset.assignment_count ?? 0) + (preset.group_count ?? 0) > 0)) return null;
	if (installed.length > 0) {
		return {
			description: `Assign ${installed[0].name} to users or groups so it shows on Generate.`,
			href: presetHref(installed[0].id)
		};
	}
	const count = matching.length === 1 ? '1 preset uses' : `${matching.length} presets use`;
	return {
		description: `${count} this backend. Install one and assign it to users or groups so it shows on Generate.`,
		href: presetHref(matching[0].id)
	};
}
