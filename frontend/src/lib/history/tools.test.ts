import { beforeEach, describe, expect, it } from 'vitest';
import {
	buildHistoryToolContext,
	HISTORY_TOOL_FALLBACK_ICON,
	listHistoryToolGroups,
	listHistoryTools,
	pluginHistoryToolFromManifest,
	registerHistoryTool,
	registerHistoryToolCategory,
	unregisterHistoryTools,
	type HistoryTool,
	type HistoryToolContext,
	type PluginHistoryToolEntry
} from './tools';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import { hasIcon } from '$lib/utils/IconLibrary';

function file(id: number, fileType: string, isFinal = true): GenerationFile {
	return {
		id,
		file_path: `out/${id}.bin`,
		file_type: fileType,
		is_final: isFinal,
		created_at: '2026-09-09T00:00:00Z'
	};
}

function generation(id: string, files: GenerationFile[]): GenerationHistoryItem {
	return {
		id,
		form_data: {},
		status: 'completed',
		progress: 100,
		created_at: '2026-09-09T00:00:00Z',
		updated_at: '2026-09-09T00:00:00Z',
		files,
		rating: 0,
		is_favorite: false
	};
}

function tool(id: string, category: string, extra: Partial<HistoryTool> = {}): HistoryTool {
	return {
		id,
		label: id,
		icon: 'layers',
		category,
		source: 'core',
		applies: () => ({ enabled: true }),
		...extra
	};
}

const emptyContext: HistoryToolContext = buildHistoryToolContext([], [], null);

beforeEach(() => {
	unregisterHistoryTools(() => true);
});

describe('buildHistoryToolContext', () => {
	it('keeps only final files and records their index within the generation', () => {
		const preview = file(1, 'IMAGE', false);
		const final = file(2, 'IMAGE');
		const ctx = buildHistoryToolContext([generation('a', [preview, final])], ['a'], null);

		expect(ctx.files).toHaveLength(1);
		expect(ctx.files[0].file.id).toBe(2);
		// The params index is the position in `generation.files`, not in `ctx.files`.
		expect(ctx.files[0].index).toBe(1);
	});

	it('follows the selection order, not the list order', () => {
		const generations = [
			generation('a', [file(1, 'IMAGE')]),
			generation('b', [file(2, 'IMAGE')]),
			generation('c', [file(3, 'IMAGE')])
		];
		const ctx = buildHistoryToolContext(generations, ['c', 'a'], null);

		expect(ctx.generationIds).toEqual(['c', 'a']);
		expect(ctx.generations.map((gen) => gen.id)).toEqual(['c', 'a']);
		expect(ctx.files.map((entry) => entry.file.id)).toEqual([3, 1]);
	});

	it('drops selected ids with no loaded generation so the two lists stay parallel', () => {
		const ctx = buildHistoryToolContext([generation('a', [])], ['a', 'gone'], null);

		expect(ctx.generationIds).toEqual(['a']);
		expect(ctx.generations).toHaveLength(1);
	});

	it('classifies each kind whatever casing the file_type arrived in', () => {
		const generations = [
			generation('a', [file(1, 'IMAGE'), file(2, 'video')]),
			generation('b', [file(3, 'AUDIO'), file(4, 'mesh')])
		];
		const ctx = buildHistoryToolContext(generations, ['a', 'b'], null);

		expect([...ctx.kinds].sort()).toEqual(['audio', 'image', 'mesh', 'video']);
		expect(ctx.files.map((entry) => entry.kind)).toEqual(['image', 'video', 'audio', 'mesh']);
	});

	it('ignores a file whose type is not a media kind', () => {
		const ctx = buildHistoryToolContext(
			[generation('a', [file(1, 'JSON'), file(2, 'IMAGE')])],
			['a'],
			null
		);

		expect(ctx.files.map((entry) => entry.file.id)).toEqual([2]);
		expect([...ctx.kinds]).toEqual(['image']);
	});

	it('carries the browsed collection through', () => {
		expect(buildHistoryToolContext([], [], 'col-1').collectionId).toBe('col-1');
	});
});

describe('registerHistoryTool', () => {
	it('replaces an existing id in place rather than adding a second entry', () => {
		registerHistoryTool(tool('first', 'analyze'));
		registerHistoryTool(tool('stitch', 'compose'));
		registerHistoryTool(tool('first', 'analyze', { label: 'Renamed' }));

		const ids = listHistoryTools().map((entry) => entry.id);
		expect(ids).toEqual(['first', 'stitch']);
		expect(listHistoryTools()[0].label).toBe('Renamed');
	});
});

describe('listHistoryToolGroups', () => {
	it('orders the declared categories and omits the empty ones', () => {
		registerHistoryTool(tool('export-zip', 'export'));
		registerHistoryTool(tool('compare', 'analyze'));

		const groups = listHistoryToolGroups(emptyContext);
		expect(groups.map((group) => group.category.id)).toEqual(['analyze', 'export']);
		expect(groups.map((group) => group.category.label)).toEqual(['Analyze', 'Export']);
	});

	it('keeps registration order within a category', () => {
		registerHistoryTool(tool('second', 'analyze'));
		registerHistoryTool(tool('first', 'analyze'));

		const [group] = listHistoryToolGroups(emptyContext);
		expect(group.tools.map((entry) => entry.tool.id)).toEqual(['second', 'first']);
	});

	it('invents a title-cased category for an unknown id and sorts it last', () => {
		registerHistoryTool(tool('compare', 'analyze'));
		registerHistoryTool(tool('wild', 'wild_things'));
		registerHistoryTool(tool('odder', 'even-odder'));

		const groups = listHistoryToolGroups(emptyContext);
		expect(groups.map((group) => group.category.id)).toEqual([
			'analyze',
			'wild_things',
			'even-odder'
		]);
		expect(groups.map((group) => group.category.label)).toEqual([
			'Analyze',
			'Wild Things',
			'Even Odder'
		]);
		expect(groups.map((group) => group.category.order)).toEqual([10, 100, 101]);
	});

	it('uses a registered category instead of inventing one', () => {
		registerHistoryToolCategory({ id: 'inspect', label: 'Inspect', order: 5 });
		registerHistoryTool(tool('peek', 'inspect'));
		registerHistoryTool(tool('compare', 'analyze'));

		const groups = listHistoryToolGroups(emptyContext);
		expect(groups.map((group) => group.category.id)).toEqual(['inspect', 'analyze']);
	});

	it('reports the availability of every tool for the given context', () => {
		registerHistoryTool(
			tool('pair', 'analyze', {
				applies: (ctx) =>
					ctx.generations.length === 2
						? { enabled: true }
						: { enabled: false, reason: 'Select exactly 2 generations' }
			})
		);

		const [group] = listHistoryToolGroups(emptyContext);
		expect(group.tools[0].availability).toEqual({
			enabled: false,
			reason: 'Select exactly 2 generations'
		});
	});
});

describe('pluginHistoryToolFromManifest', () => {
	function entry(overrides: Partial<PluginHistoryToolEntry> = {}): PluginHistoryToolEntry {
		return {
			id: 'stitcher:join',
			plugin_id: 'stitcher',
			label: 'Join clips',
			category: 'compose',
			component: 'plugin:stitcher:JoinClips.js',
			...overrides
		};
	}

	const imageOnly = buildHistoryToolContext(
		[generation('a', [file(1, 'IMAGE')]), generation('b', [file(2, 'IMAGE')])],
		['a', 'b'],
		null
	);
	const oneVideo = buildHistoryToolContext([generation('a', [file(1, 'VIDEO')])], ['a'], null);

	it('keeps the served id and component ref as they are', () => {
		const built = pluginHistoryToolFromManifest(entry());
		expect(built.id).toBe('stitcher:join');
		expect(built.componentRef).toBe('plugin:stitcher:JoinClips.js');
		expect(built.source).toBe('plugin');
	});

	it('prefixes a bare asset with its plugin so it parses as a component ref', () => {
		expect(pluginHistoryToolFromManifest(entry({ component: 'JoinClips.js' })).componentRef).toBe(
			'plugin:stitcher:JoinClips.js'
		);
	});

	it('keeps an icon the icon library can draw', () => {
		expect(pluginHistoryToolFromManifest(entry({ icon: 'film' })).icon).toBe('film');
	});

	it('swaps an undrawable icon for the fallback rather than rendering a letter', () => {
		// `tool` is the manifest schema's own default and has no path in IconLibrary.
		expect(pluginHistoryToolFromManifest(entry({ icon: 'tool' })).icon).toBe(
			HISTORY_TOOL_FALLBACK_ICON
		);
		expect(pluginHistoryToolFromManifest(entry({ icon: 'no-such-icon' })).icon).toBe(
			HISTORY_TOOL_FALLBACK_ICON
		);
		expect(pluginHistoryToolFromManifest(entry({ icon: '' })).icon).toBe(
			HISTORY_TOOL_FALLBACK_ICON
		);
		expect(hasIcon(HISTORY_TOOL_FALLBACK_ICON)).toBe(true);
	});

	it('is unconstrained when applies_to is absent', () => {
		expect(pluginHistoryToolFromManifest(entry()).applies(imageOnly)).toEqual({ enabled: true });
		expect(pluginHistoryToolFromManifest(entry()).applies(emptyContext)).toEqual({ enabled: true });
	});

	it('enforces min_selection', () => {
		const built = pluginHistoryToolFromManifest(entry({ applies_to: { min_selection: 3 } }));
		expect(built.applies(imageOnly)).toEqual({
			enabled: false,
			reason: 'Select at least 3 generations'
		});
		expect(built.applies(oneVideo).enabled).toBe(false);
	});

	it('enforces max_selection', () => {
		const built = pluginHistoryToolFromManifest(entry({ applies_to: { max_selection: 1 } }));
		expect(built.applies(imageOnly)).toEqual({
			enabled: false,
			reason: 'Select at most 1 generation'
		});
		expect(built.applies(oneVideo)).toEqual({ enabled: true });
	});

	it('enforces media_kinds against the selected files', () => {
		const built = pluginHistoryToolFromManifest(entry({ applies_to: { media_kinds: ['video'] } }));
		expect(built.applies(imageOnly)).toEqual({
			enabled: false,
			reason: 'Select at least one video'
		});
		expect(built.applies(oneVideo)).toEqual({ enabled: true });
	});

	it('accepts a selection carrying any one of several media_kinds', () => {
		const built = pluginHistoryToolFromManifest(
			entry({ applies_to: { media_kinds: ['video', 'audio'] } })
		);
		expect(built.applies(oneVideo)).toEqual({ enabled: true });
		expect(built.applies(imageOnly)).toEqual({
			enabled: false,
			reason: 'Select at least one video or audio'
		});
	});
});
