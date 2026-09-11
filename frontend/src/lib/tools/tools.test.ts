import { beforeEach, describe, expect, it } from 'vitest';
import {
	buildHistoryToolContext,
	buildLibraryToolContext,
	listTools,
	listToolGroups,
	MEDIA_TOOL_FALLBACK_ICON,
	pluginMediaToolFromManifest,
	registerMediaTool,
	registerMediaToolCategory,
	unregisterMediaTools,
	type MediaTool,
	type MediaToolContext,
	type PluginMediaToolEntry
} from './tools';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import type { LibraryItem } from '$lib/services/api/library';
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

function libraryItem(id: string, mediaType = 'image', overrides: Partial<LibraryItem> = {}): LibraryItem {
	return {
		id,
		filename: `${id}.png`,
		media_type: mediaType,
		url: `/api/media/uploads/${id}.png`,
		tags: [],
		...overrides
	};
}

function tool(id: string, category: string, extra: Partial<MediaTool> = {}): MediaTool {
	return {
		id,
		label: id,
		icon: 'layers',
		category,
		source: 'core',
		scopes: ['history'],
		applies: () => ({ enabled: true }),
		...extra
	};
}

const emptyContext: MediaToolContext = buildHistoryToolContext([], [], null);

beforeEach(() => {
	unregisterMediaTools(() => true);
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

	it('is scoped to history and mirrors each final file into items with a generation ref', () => {
		const ctx = buildHistoryToolContext([generation('a', [file(1, 'IMAGE')])], ['a'], null);

		expect(ctx.scope).toBe('history');
		expect(ctx.items).toEqual([
			{
				id: 'a:0',
				kind: 'image',
				url: '/api/media/generations/a/1.bin',
				filename: '1.bin',
				width: undefined,
				height: undefined,
				generationId: 'a',
				paramIndex: 0
			}
		]);
	});
});

describe('buildLibraryToolContext', () => {
	it('is scoped to library with no generation fields', () => {
		const ctx = buildLibraryToolContext([libraryItem('x')], null);

		expect(ctx.scope).toBe('library');
		expect(ctx.generations).toEqual([]);
		expect(ctx.generationIds).toEqual([]);
		expect(ctx.files).toEqual([]);
	});

	it('maps each item to a MediaToolItem carrying no generation ref', () => {
		const ctx = buildLibraryToolContext(
			[libraryItem('x', 'video', { original_filename: 'clip.mp4', width: 10, height: 20 })],
			null
		);

		expect(ctx.items).toEqual([
			{
				id: 'x',
				kind: 'video',
				url: '/api/media/uploads/x.png',
				filename: 'clip.mp4',
				width: 10,
				height: 20
			}
		]);
		expect([...ctx.kinds]).toEqual(['video']);
	});

	it('falls back to the storage filename when there is no original filename', () => {
		const ctx = buildLibraryToolContext([libraryItem('x')], null);
		expect(ctx.items[0].filename).toBe('x.png');
	});

	it('drops an item whose media type is not image/video/audio', () => {
		const ctx = buildLibraryToolContext([libraryItem('x', 'mesh')], null);
		expect(ctx.items).toEqual([]);
	});

	it('carries the browsed collection through', () => {
		expect(buildLibraryToolContext([], 'col-1').collectionId).toBe('col-1');
	});
});

describe('registerMediaTool', () => {
	it('replaces an existing id in place rather than adding a second entry', () => {
		registerMediaTool(tool('first', 'analyze'));
		registerMediaTool(tool('stitch', 'compose'));
		registerMediaTool(tool('first', 'analyze', { label: 'Renamed' }));

		const ids = listTools().map((entry) => entry.id);
		expect(ids).toEqual(['first', 'stitch']);
		expect(listTools()[0].label).toBe('Renamed');
	});
});

describe('listTools', () => {
	it('returns every registered tool when no scope is given', () => {
		registerMediaTool(tool('history-only', 'analyze', { scopes: ['history'] }));
		registerMediaTool(tool('library-only', 'analyze', { scopes: ['library'] }));

		expect(listTools().map((t) => t.id)).toEqual(['history-only', 'library-only']);
	});

	it('narrows to the tools offered on the given scope', () => {
		registerMediaTool(tool('history-only', 'analyze', { scopes: ['history'] }));
		registerMediaTool(tool('both', 'analyze', { scopes: ['history', 'library'] }));
		registerMediaTool(tool('library-only', 'analyze', { scopes: ['library'] }));

		expect(listTools('history').map((t) => t.id)).toEqual(['history-only', 'both']);
		expect(listTools('library').map((t) => t.id)).toEqual(['both', 'library-only']);
	});
});

describe('listToolGroups', () => {
	it('orders the declared categories and omits the empty ones', () => {
		registerMediaTool(tool('export-zip', 'export'));
		registerMediaTool(tool('compare', 'analyze'));

		const groups = listToolGroups(emptyContext);
		expect(groups.map((group) => group.category.id)).toEqual(['analyze', 'export']);
		expect(groups.map((group) => group.category.label)).toEqual(['Analyze', 'Export']);
	});

	it('keeps registration order within a category', () => {
		registerMediaTool(tool('second', 'analyze'));
		registerMediaTool(tool('first', 'analyze'));

		const [group] = listToolGroups(emptyContext);
		expect(group.tools.map((entry) => entry.tool.id)).toEqual(['second', 'first']);
	});

	it('invents a title-cased category for an unknown id and sorts it last', () => {
		registerMediaTool(tool('compare', 'analyze'));
		registerMediaTool(tool('wild', 'wild_things'));
		registerMediaTool(tool('odder', 'even-odder'));

		const groups = listToolGroups(emptyContext);
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
		registerMediaToolCategory({ id: 'inspect', label: 'Inspect', order: 5 });
		registerMediaTool(tool('peek', 'inspect'));
		registerMediaTool(tool('compare', 'analyze'));

		const groups = listToolGroups(emptyContext);
		expect(groups.map((group) => group.category.id)).toEqual(['inspect', 'analyze']);
	});

	it('reports the availability of every tool for the given context', () => {
		registerMediaTool(
			tool('pair', 'analyze', {
				applies: (ctx) =>
					ctx.generations.length === 2
						? { enabled: true }
						: { enabled: false, reason: 'Select exactly 2 generations' }
			})
		);

		const [group] = listToolGroups(emptyContext);
		expect(group.tools[0].availability).toEqual({
			enabled: false,
			reason: 'Select exactly 2 generations'
		});
	});

	it('leaves out a tool not offered on the context scope entirely, not just disabled', () => {
		registerMediaTool(tool('history-only', 'analyze', { scopes: ['history'] }));

		const libraryCtx = buildLibraryToolContext([], null);
		expect(listToolGroups(libraryCtx)).toEqual([]);

		const historyCtx = buildHistoryToolContext([], [], null);
		expect(listToolGroups(historyCtx)[0].tools.map((entry) => entry.tool.id)).toEqual([
			'history-only'
		]);
	});
});

describe('pluginMediaToolFromManifest', () => {
	function entry(overrides: Partial<PluginMediaToolEntry> = {}): PluginMediaToolEntry {
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
		const built = pluginMediaToolFromManifest(entry());
		expect(built.id).toBe('stitcher:join');
		expect(built.componentRef).toBe('plugin:stitcher:JoinClips.js');
		expect(built.source).toBe('plugin');
	});

	it('prefixes a bare asset with its plugin so it parses as a component ref', () => {
		expect(pluginMediaToolFromManifest(entry({ component: 'JoinClips.js' })).componentRef).toBe(
			'plugin:stitcher:JoinClips.js'
		);
	});

	it('keeps an icon the icon library can draw', () => {
		expect(pluginMediaToolFromManifest(entry({ icon: 'film' })).icon).toBe('film');
	});

	it('swaps an undrawable icon for the fallback rather than rendering a letter', () => {
		// `tool` is the manifest schema's own default and has no path in IconLibrary.
		expect(pluginMediaToolFromManifest(entry({ icon: 'tool' })).icon).toBe(
			MEDIA_TOOL_FALLBACK_ICON
		);
		expect(pluginMediaToolFromManifest(entry({ icon: 'no-such-icon' })).icon).toBe(
			MEDIA_TOOL_FALLBACK_ICON
		);
		expect(pluginMediaToolFromManifest(entry({ icon: '' })).icon).toBe(MEDIA_TOOL_FALLBACK_ICON);
		expect(hasIcon(MEDIA_TOOL_FALLBACK_ICON)).toBe(true);
	});

	it('defaults to the history scope when the manifest predates scopes', () => {
		expect(pluginMediaToolFromManifest(entry()).scopes).toEqual(['history']);
		expect(pluginMediaToolFromManifest(entry({ scopes: [] })).scopes).toEqual(['history']);
	});

	it('keeps an explicit scopes list from the manifest', () => {
		expect(pluginMediaToolFromManifest(entry({ scopes: ['history', 'library'] })).scopes).toEqual([
			'history',
			'library'
		]);
	});

	it('is unconstrained when applies_to is absent', () => {
		expect(pluginMediaToolFromManifest(entry()).applies(imageOnly)).toEqual({ enabled: true });
		expect(pluginMediaToolFromManifest(entry()).applies(emptyContext)).toEqual({ enabled: true });
	});

	it('enforces min_selection', () => {
		const built = pluginMediaToolFromManifest(entry({ applies_to: { min_selection: 3 } }));
		expect(built.applies(imageOnly)).toEqual({
			enabled: false,
			reason: 'Select at least 3 generations'
		});
		expect(built.applies(oneVideo).enabled).toBe(false);
	});

	it('enforces max_selection', () => {
		const built = pluginMediaToolFromManifest(entry({ applies_to: { max_selection: 1 } }));
		expect(built.applies(imageOnly)).toEqual({
			enabled: false,
			reason: 'Select at most 1 generation'
		});
		expect(built.applies(oneVideo)).toEqual({ enabled: true });
	});

	it('enforces media_kinds against the selected files', () => {
		const built = pluginMediaToolFromManifest(entry({ applies_to: { media_kinds: ['video'] } }));
		expect(built.applies(imageOnly)).toEqual({
			enabled: false,
			reason: 'Select at least one video'
		});
		expect(built.applies(oneVideo)).toEqual({ enabled: true });
	});

	it('accepts a selection carrying any one of several media_kinds', () => {
		const built = pluginMediaToolFromManifest(
			entry({ applies_to: { media_kinds: ['video', 'audio'] } })
		);
		expect(built.applies(oneVideo)).toEqual({ enabled: true });
		expect(built.applies(imageOnly)).toEqual({
			enabled: false,
			reason: 'Select at least one video or audio'
		});
	});

	it('counts against items, not generations, on the library scope', () => {
		const built = pluginMediaToolFromManifest(entry({ applies_to: { min_selection: 2 } }));
		const oneLibraryItem = buildLibraryToolContext([libraryItem('a')], null);
		const twoLibraryItems = buildLibraryToolContext([libraryItem('a'), libraryItem('b')], null);

		expect(built.applies(oneLibraryItem)).toEqual({
			enabled: false,
			reason: 'Select at least 2 items'
		});
		expect(built.applies(twoLibraryItems)).toEqual({ enabled: true });
	});
});
