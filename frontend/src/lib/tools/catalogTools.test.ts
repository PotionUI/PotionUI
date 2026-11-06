import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/services/api/mediaTools', () => ({ listMediaTools: vi.fn() }));
vi.mock('$lib/stores/toast', () => ({ toasts: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock('../../routes/history/components/HistoryApplyLookModal.svelte', () => ({ default: {} }));
vi.mock('../../routes/history/components/HistoryCompareModal.svelte', () => ({ default: {} }));
vi.mock('../../routes/history/components/HistoryEditImageModal.svelte', () => ({ default: {} }));
vi.mock('../../routes/history/components/HistoryExportZipModal.svelte', () => ({ default: {} }));
vi.mock('../../routes/history/components/HistoryStitchModal.svelte', () => ({ default: {} }));

import {
	buildHistoryToolContext,
	buildLibraryToolContext,
	groupToolsFor,
	listTools,
	unregisterMediaTools,
	type MediaToolContext,
	type MediaToolGroup
} from './tools';
import {
	catalogAvailability,
	isCatalogTool,
	mediaToolFromCatalog,
	MEDIA_TOOL_FALLBACK_ICON,
	syncCatalogTools,
	toolNote
} from './catalogTools';
import { buildHistoryEntryContext, buildLibraryEntryContext } from './entryMenu';
import { registerEntryTools } from './entryTools';
import { registerCoreHistoryTools } from '../../routes/history/tools/coreTools';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
import type { LibraryItem } from '$lib/services/api/library';
import { A1111_TOOL, BOOMERANG_TOOL, CORE_TOOLS, FIX_COLORS_TOOL, toolDto } from '../../../tests/component/fixtures/mediaToolCatalog';

registerEntryTools();
registerCoreHistoryTools();

function file(id: number, type: string, extra: Partial<GenerationFile> = {}): GenerationFile {
	return {
		id,
		file_path: `out/${id}.${type === 'image' ? 'png' : type === 'video' ? 'mp4' : 'wav'}`,
		file_type: type,
		is_final: true,
		created_at: '2026-09-09T00:00:00Z',
		...extra
	};
}

function generation(id: string, files: GenerationFile[]): GenerationHistoryItem {
	return {
		id,
		preset_id: 'p',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: '2026-09-09T00:00:00Z',
		updated_at: '2026-09-09T00:00:00Z',
		files,
		rating: 0,
		is_favorite: false
	} as GenerationHistoryItem;
}

function libraryItem(id: string, mediaType: string, extra: Partial<LibraryItem> = {}): LibraryItem {
	const ext = mediaType === 'image' ? 'png' : mediaType === 'video' ? 'mp4' : 'wav';
	return {
		id,
		filename: `${id}.${ext}`,
		original_filename: `${id}.${ext}`,
		media_type: mediaType,
		url: `/api/media/uploads/${id}.${ext}`,
		tags: [],
		...extra
	};
}

function historyCtx(kind: string, count = 1): MediaToolContext {
	const generations = Array.from({ length: count }, (_, index) =>
		generation(`g${index}`, [file(index + 1, kind)])
	);
	return buildHistoryToolContext(generations, generations.map((entry) => entry.id), null);
}

function libraryCtx(kind: string, count = 1): MediaToolContext {
	return buildLibraryToolContext(
		Array.from({ length: count }, (_, index) => libraryItem(`l${index}`, kind)),
		null
	);
}

function names(groups: MediaToolGroup[]): Record<string, string[]> {
	return Object.fromEntries(
		groups.map((group) => [group.category.id, group.tools.map(({ tool }) => tool.id)])
	);
}

beforeEach(() => {
	unregisterMediaTools((tool) => !!tool.catalogToolId);
	syncCatalogTools([...CORE_TOOLS, A1111_TOOL, BOOMERANG_TOOL]);
});

describe('mediaToolFromCatalog', () => {
	it('files a runnable tool under Edit and keeps its id and kinds', () => {
		const mapped = mediaToolFromCatalog(CORE_TOOLS[1]);
		expect(mapped).toMatchObject({
			id: 'crop',
			label: 'Crop',
			category: 'edit',
			source: 'core',
			kinds: ['image', 'video'],
			catalogToolId: 'crop',
			builtInEditor: true
		});
		expect(isCatalogTool(mapped)).toBe(true);
	});

	it('files a runnable tool in the effects group under Effects', () => {
		expect(mediaToolFromCatalog(FIX_COLORS_TOOL).category).toBe('effects');
		expect(mediaToolFromCatalog({ ...FIX_COLORS_TOOL, runnable: false }).category).toBe('plugins');
	});

	it('files a component-only plugin tool under Plugins', () => {
		const mapped = mediaToolFromCatalog(A1111_TOOL);
		expect(mapped.category).toBe('plugins');
		expect(mapped.source).toBe('plugin');
		expect(mapped.catalogToolId).toBe(A1111_TOOL.id);
	});

	it('files a plugin tool that has an implementation under Edit', () => {
		const mapped = mediaToolFromCatalog(BOOMERANG_TOOL);
		expect(mapped.category).toBe('edit');
		expect(mapped.source).toBe('plugin');
		expect(mapped.builtInEditor).toBe(false);
	});

	it('keeps the page scopes and drops field, which the field toolbar owns', () => {
		expect(mediaToolFromCatalog(CORE_TOOLS[0]).scopes).toEqual(['library', 'history']);
		expect(mediaToolFromCatalog(toolDto({ id: 'x', scopes: ['field'] })).scopes).toEqual([]);
	});

	it('defaults the selection to one item, or two or more for a many-to-one tool', () => {
		expect(mediaToolFromCatalog(CORE_TOOLS[0]).selection).toEqual({ min: 1 });
		expect(mediaToolFromCatalog(CORE_TOOLS[11]).selection).toEqual({ min: 2, max: 8 });
		expect(mediaToolFromCatalog(toolDto({ id: 'm', cardinality: 'many_to_one' })).selection).toEqual({
			min: 2
		});
	});

	it('swaps an icon the library cannot draw for the fallback', () => {
		expect(mediaToolFromCatalog(toolDto({ id: 'a', icon: 'no-such-icon' })).icon).toBe(
			MEDIA_TOOL_FALLBACK_ICON
		);
		expect(mediaToolFromCatalog(toolDto({ id: 'a', icon: '' })).icon).toBe(MEDIA_TOOL_FALLBACK_ICON);
		expect(mediaToolFromCatalog(toolDto({ id: 'a', icon: 'film' })).icon).toBe('film');
	});
});

describe('toolNote', () => {
	it('says what a tool saves when it is not a plain edit of the same file', () => {
		expect(toolNote(CORE_TOOLS[9])).toBe('Saves one image');
		expect(toolNote(CORE_TOOLS[10])).toBe('Saves several files');
		expect(toolNote(CORE_TOOLS[0])).toBe('');
	});

	it('names the plugin for a plugin tool', () => {
		expect(toolNote(A1111_TOOL)).toBe('A1111 Metadata Export');
		expect(toolNote(BOOMERANG_TOOL)).toBe('Boomerang');
	});
});

describe('catalogAvailability', () => {
	const concat = CORE_TOOLS[11];
	const crop = CORE_TOOLS[1];
	const cropSubject = CORE_TOOLS[8];

	it('hides a tool that accepts none of the selected kinds', () => {
		expect(catalogAvailability(crop, libraryCtx('audio'))).toEqual({ enabled: false, hidden: true });
	});

	it('disables a many-to-one tool below its minimum with the range as the reason', () => {
		expect(catalogAvailability(concat, libraryCtx('video', 1))).toEqual({
			enabled: false,
			hidden: false,
			reason: 'Select 2 to 8 items'
		});
		expect(catalogAvailability(concat, libraryCtx('video', 2))).toEqual({ enabled: true });
		expect(catalogAvailability(concat, libraryCtx('video', 9)).enabled).toBe(false);
	});

	it('hides a tool that can never be satisfied by a single card', () => {
		const entry = buildLibraryEntryContext(libraryItem('a', 'video'), null);
		expect(catalogAvailability(concat, entry).hidden).toBe(true);
	});

	it('lets a tool with a built-in editor work on one item only', () => {
		expect(catalogAvailability(crop, libraryCtx('image', 2))).toEqual({
			enabled: false,
			reason: 'Select one item'
		});
		expect(catalogAvailability(crop, libraryCtx('image', 1))).toEqual({ enabled: true });
	});

	it('lets a tool without an editor take a batch', () => {
		expect(catalogAvailability(CORE_TOOLS[3], libraryCtx('image', 3))).toEqual({ enabled: true });
	});

	it('disables a mixed selection with the kind it cannot take', () => {
		const mixed = buildLibraryToolContext([libraryItem('a', 'image'), libraryItem('b', 'video')], null);
		const result = catalogAvailability(CORE_TOOLS[8], mixed);
		expect(result.enabled).toBe(false);
		expect(result.reason).toBe('Not available for video');
	});

	it('needs transparency for crop to subject, which a JPEG cannot have', () => {
		const jpeg = buildLibraryToolContext([libraryItem('a', 'image', { original_filename: 'photo.jpg' })], null);
		expect(catalogAvailability(cropSubject, jpeg)).toMatchObject({
			enabled: false,
			reason: 'Needs a transparent image'
		});
		expect(catalogAvailability(cropSubject, libraryCtx('image'))).toEqual({ enabled: true });
	});

	it('reads transparency from a generated file', () => {
		const opaque = buildHistoryToolContext(
			[generation('g', [file(1, 'image', { has_alpha: false })])],
			['g'],
			null
		);
		const alpha = buildHistoryToolContext(
			[generation('g', [file(1, 'image', { has_alpha: true })])],
			['g'],
			null
		);
		expect(catalogAvailability(cropSubject, opaque).enabled).toBe(false);
		expect(catalogAvailability(cropSubject, alpha).enabled).toBe(true);
	});

	it('counts generations rather than files for a component-only tool on History', () => {
		const fourImages = generation('g', [1, 2, 3, 4].map((id) => file(id, 'image')));
		const ctx = buildHistoryToolContext([fourImages], ['g'], null);
		const single = toolDto({ ...A1111_TOOL, id: 'p.single', selection: { min: 1, max: 1 } });
		expect(ctx.items).toHaveLength(4);
		expect(catalogAvailability(A1111_TOOL, ctx)).toEqual({ enabled: true });
		expect(catalogAvailability(single, ctx)).toEqual({ enabled: true });
	});

	it('gives a component-only tool its own selection bounds in generation words', () => {
		const tool = toolDto({
			...A1111_TOOL,
			id: 'p.pair',
			selection: { min: 2, max: 2 }
		});
		expect(catalogAvailability(tool, historyCtx('image', 1))).toMatchObject({
			enabled: false,
			reason: 'Select 2 generations'
		});
		expect(catalogAvailability(tool, historyCtx('image', 2))).toEqual({ enabled: true });
	});
});

describe('syncCatalogTools', () => {
	it('mirrors the catalog onto the registry and leaves core registrations alone', () => {
		const ids = listTools().map((tool) => tool.id);
		expect(ids).toEqual(expect.arrayContaining(['trim', 'concat', 'boomerang.loop', 'compare', 'edit-image']));
		expect(listTools('history').map((tool) => tool.id)).toContain(A1111_TOOL.id);
		expect(listTools('library').map((tool) => tool.id)).not.toContain(A1111_TOOL.id);
	});

	it('replaces the previous snapshot so a removed plugin tool disappears', () => {
		syncCatalogTools(CORE_TOOLS);
		const ids = listTools().map((tool) => tool.id);
		expect(ids).not.toContain('boomerang.loop');
		expect(ids).not.toContain(A1111_TOOL.id);
		expect(ids).toContain('trim');
		expect(ids).toContain('compare');
	});

	it('does not register a tool that is offered on no page', () => {
		syncCatalogTools([toolDto({ id: 'only-field', scopes: ['field'] })]);
		expect(listTools().map((tool) => tool.id)).not.toContain('only-field');
	});
});

describe('the Edit and Effects groups', () => {
	const bar = (ctx: MediaToolContext) => names(groupToolsFor(ctx, { surface: 'bar' }));
	const edit = (ctx: MediaToolContext) => bar(ctx).edit ?? [];

	it('offers a video the video tools, in catalog order', () => {
		expect(edit(libraryCtx('video'))).toEqual([
			'trim',
			'crop',
			'resize',
			'rotate',
			'flip',
			'speed',
			'frame',
			'concat',
			'boomerang.loop'
		]);
	});

	it('offers an image the image tools', () => {
		expect(edit(libraryCtx('image'))).toEqual([
			'crop',
			'resize',
			'rotate',
			'flip',
			'fit_canvas',
			'slice_sheet',
			'color_key',
			'crop_subject'
		]);
	});

	it('offers audio trim, speed and split', () => {
		expect(edit(libraryCtx('audio'))).toEqual(['trim', 'speed', 'split']);
	});

	it('shows Effects for an image only', () => {
		expect(bar(libraryCtx('image')).effects).toEqual(['apply-look']);
		expect(bar(libraryCtx('video')).effects).toBeUndefined();
		expect(bar(libraryCtx('audio')).effects).toBeUndefined();
	});

	it('puts Edit and Effects ahead of every existing group', () => {
		const order = groupToolsFor(libraryCtx('image', 2), { surface: 'bar' }).map((group) => group.category.id);
		expect(order.slice(0, 2)).toEqual(['edit', 'effects']);
		expect(order).toEqual(expect.arrayContaining(['analyze', 'compose', 'export']));
	});

	it('shows both pages the same Edit and Effects groups for the same kind of media', () => {
		for (const kind of ['image', 'video', 'audio']) {
			const library = bar(libraryCtx(kind));
			const history = bar(historyCtx(kind));
			expect(history.edit).toEqual(library.edit);
			expect(history.effects).toEqual(library.effects);
		}
	});

	it('keeps the order of the groups the same on both pages', () => {
		const order = (ctx: MediaToolContext) =>
			groupToolsFor(ctx, { surface: 'bar' })
				.map((group) => group.category.id)
				.filter((id) => ['edit', 'effects', 'analyze', 'compose', 'export'].includes(id));
		expect(order(historyCtx('image', 2))).toEqual(order(libraryCtx('image', 2)));
	});

	it('offers the A1111 export on History only', () => {
		expect(bar(historyCtx('image')).plugins).toEqual([A1111_TOOL.id]);
		expect(bar(libraryCtx('image')).plugins).toBeUndefined();
	});

	it('offers the A1111 export to images and not to video', () => {
		expect(bar(historyCtx('video')).plugins).toBeUndefined();
	});

	it('keeps tools the selection cannot take disabled with a reason on the bar', () => {
		const groups = groupToolsFor(libraryCtx('video'), { surface: 'bar' });
		const concat = groups.flatMap((group) => group.tools).find(({ tool }) => tool.id === 'concat');
		expect(concat?.availability).toMatchObject({ enabled: false, reason: 'Select 2 to 8 items' });
	});
});

describe('the card menu', () => {
	const card = (ctx: MediaToolContext) =>
		names(groupToolsFor(ctx, { surface: 'entry', host: {} }));

	it('starts with the same Edit and Effects groups on both pages', () => {
		const library = card(buildLibraryEntryContext(libraryItem('a', 'video'), null));
		const history = buildHistoryEntryContext(
			generation('g', [file(1, 'video')]),
			generation('g', [file(1, 'video')]).files[0],
			null
		);
		const historyGroups = names(groupToolsFor(history, { surface: 'entry', host: {} }));
		expect(historyGroups.edit).toEqual(library.edit);
		expect(library.edit).toEqual(['trim', 'crop', 'resize', 'rotate', 'flip', 'speed', 'frame', 'boomerang.loop']);
	});

	it('hides what one card can never satisfy', () => {
		const library = card(buildLibraryEntryContext(libraryItem('a', 'video'), null));
		expect(library.edit).not.toContain('concat');
	});

	it('shows an image card its image tools and the look', () => {
		const groups = card(buildLibraryEntryContext(libraryItem('a', 'image'), null));
		expect(groups.edit).toContain('crop');
		expect(groups.edit).not.toContain('trim');
		expect(groups.effects).toEqual(['apply-look']);
	});

	it('shows an audio card no Effects', () => {
		const groups = card(buildLibraryEntryContext(libraryItem('a', 'audio'), null));
		expect(groups.effects).toBeUndefined();
		expect(groups.edit).toEqual(['trim', 'speed', 'split']);
	});

	it('places Plugins right after Effects on a card and after Export on the bar', () => {
		const cardOrder = groupToolsFor(
			buildHistoryEntryContext(
				generation('g', [file(1, 'image')]),
				file(1, 'image'),
				null
			),
			{ surface: 'entry', host: {} }
		).map((group) => group.category.id);
		const barOrder = groupToolsFor(historyCtx('image'), { surface: 'bar' }).map((group) => group.category.id);
		expect(cardOrder.indexOf('plugins')).toBe(cardOrder.indexOf('effects') + 1);
		expect(barOrder.indexOf('plugins')).toBeGreaterThan(barOrder.indexOf('export'));
	});
});
