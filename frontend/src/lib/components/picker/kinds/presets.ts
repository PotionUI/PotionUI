import type { PresetInfo } from '$lib/types/api';
import type { EntityKind } from '../types';
import { byName, plural, uniqueOptions } from './shared';

export function presetSourceLabel(preset: PresetInfo): string {
	const origin = preset.origin;
	if (origin?.kind === 'plugin') return `plugin: ${origin.plugin_id ?? 'unknown'}`;
	if (origin?.kind === 'local') return 'local';
	if (origin?.kind === 'marketplace') return 'marketplace';
	return preset.source === 'custom' ? 'local' : 'marketplace';
}

export function presetModesLabel(preset: PresetInfo): string {
	const modes = preset.modes ?? [];
	if (modes.length === 0) return '';
	if (modes.length <= 2) return modes.join(', ');
	return `${modes.slice(0, 2).join(', ')} +${modes.length - 2}`;
}

export function presetUseLabel(preset: PresetInfo): string {
	return `${plural(preset.assignment_count ?? 0, 'user')} · ${preset.group_count ?? 0} grp`;
}

export const presetsKind: EntityKind<PresetInfo> = {
	id: 'presets',
	singular: 'preset',
	plural: 'presets',
	icon: 'cube',
	getId: (preset) => preset.id,
	getName: (preset) => preset.name,
	searchText: (preset) =>
		[
			preset.name,
			preset.id,
			preset.engine,
			presetSourceLabel(preset),
			preset.origin?.path,
			preset.category,
			preset.version,
			...(preset.modes ?? [])
		]
			.filter(Boolean)
			.join(' '),
	lead: (preset) => ({
		type: 'cover',
		id: preset.id,
		name: preset.name,
		src: preset.media?.cover ?? null,
		category: preset.category ?? null
	}),
	facts: [
		{ key: 'engine', label: 'Engine', value: (p) => p.engine, as: 'tag', base: true },
		{ key: 'source', label: 'Source', value: presetSourceLabel, as: 'tag', base: true },
		{ key: 'folder', label: 'Folder', value: (p) => p.origin?.path, as: 'text' },
		{ key: 'version', label: 'Version', value: (p) => (p.version ? `v${p.version}` : ''), as: 'text' }
	],
	columns: [
		{ key: 'category', label: 'Category', width: '90px', mono: true, value: (p) => p.category ?? '' },
		{ key: 'modes', label: 'Modes', width: '170px', priority: 1, mono: true, value: presetModesLabel },
		{ key: 'use', label: 'In use', width: '130px', priority: 1, mono: true, value: presetUseLabel },
		{
			key: 'needs',
			label: 'Needs',
			width: '80px',
			priority: 2,
			mono: true,
			value: (p) => (p.requires?.min_vram_gb ? `${p.requires.min_vram_gb} GB` : '')
		}
	],
	filters: [
		{
			key: 'engine',
			label: 'Engine',
			options: (rows) => uniqueOptions(rows.map((p) => p.engine), 'Any engine'),
			match: (p, value) => p.engine === value
		},
		{
			key: 'source',
			label: 'Source',
			options: (rows) => uniqueOptions(rows.map(presetSourceLabel), 'Any source'),
			match: (p, value) => presetSourceLabel(p) === value
		},
		{
			key: 'category',
			label: 'Category',
			options: (rows) => uniqueOptions(rows.map((p) => p.category), 'Any category'),
			match: (p, value) => p.category === value
		},
		{
			key: 'mode',
			label: 'Mode',
			options: (rows) => uniqueOptions(rows.flatMap((p) => p.modes ?? []), 'Any mode'),
			match: (p, value) => (p.modes ?? []).includes(value)
		},
		{
			key: 'same_name',
			label: 'Names',
			options: () => [
				{ value: '', label: 'All names' },
				{ value: 'same', label: 'Same name only' }
			],
			match: (_p, value, ctx) => value !== 'same' || ctx.collision > 1
		}
	],
	sorts: [
		{ value: 'name', label: 'Name', compare: byName((p: PresetInfo) => p.name) },
		{
			value: 'use',
			label: 'Most used',
			compare: (a, b) => (b.assignment_count ?? 0) - (a.assignment_count ?? 0) || a.name.localeCompare(b.name)
		}
	],
	defaultSort: 'name',
	searchPlaceholder: 'Search name, engine, path, id',
	empty: {
		title: 'No presets installed',
		description: 'Install a preset from Admin, Presets first, then assign it here.'
	},
	rowHeight: 58
};
