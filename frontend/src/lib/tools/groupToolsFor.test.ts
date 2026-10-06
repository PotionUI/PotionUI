import { beforeAll, describe, expect, it, vi } from 'vitest';
import {
	buildHistoryToolContext,
	groupToolsFor,
	pluginMediaToolFromManifest,
	registerMediaTool,
	selectionCount,
	type MediaTool,
	type MediaToolContext,
	type PluginMediaToolEntry,
	type ToolRunHost
} from './tools';
import { buildHistoryEntryContext } from './entryMenu';
import { registerEntryTools } from './entryTools';
import type { GenerationHistoryItem } from '$lib/types/history';

vi.mock('$lib/stores/toast', () => ({ toasts: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

function generation(id: string, overrides: Partial<GenerationHistoryItem> = {}): GenerationHistoryItem {
	return {
		id,
		preset_id: 'p',
		form_data: { prompt: 'x' },
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files: [
			{
				id: 1,
				file_path: `out/${id}.png`,
				file_type: 'image',
				is_final: true,
				created_at: '2026-01-01T00:00:00Z'
			}
		],
		rating: 0,
		is_favorite: false,
		...overrides
	} as GenerationHistoryItem;
}

const HOST: ToolRunHost = {
	open: vi.fn(),
	reuse: vi.fn(),
	remove: vi.fn(),
	deselect: vi.fn(),
	rate: vi.fn(),
	favorite: vi.fn(),
	copyToLibrary: vi.fn(),
	addToCollection: vi.fn()
};

function stub(id: string, extra: Partial<MediaTool>): MediaTool {
	return {
		id,
		label: id,
		icon: 'layers',
		category: 'compose',
		source: 'core',
		scopes: ['history', 'library'],
		applies: () => ({ enabled: true }),
		...extra
	};
}

beforeAll(() => {
	registerEntryTools();
	registerMediaTool(stub('t-compare', { category: 'analyze', selection: { min: 2, max: 2 } }));
	registerMediaTool(stub('t-stitch', { selection: { min: 2 } }));
	registerMediaTool(stub('t-edit', { selection: { min: 1, max: 1 }, kinds: ['image'] }));
	registerMediaTool(stub('t-any', {}));
});

function ids(ctx: MediaToolContext, surface: 'entry' | 'selection' | 'bar'): string[] {
	return groupToolsFor(ctx, { surface, host: HOST }).flatMap((group) =>
		group.tools.map(({ tool }) => tool.id)
	);
}

describe('groupToolsFor', () => {
	const one = generation('a');
	const entryCtx = buildHistoryEntryContext(one, one.files[0], null);
	const manyCtx = buildHistoryToolContext(
		[generation('a'), generation('b'), generation('c')],
		['a', 'b', 'c'],
		null
	);

	it('hides every multi-item tool in the single-entry menu', () => {
		const got = ids(entryCtx, 'entry');
		expect(got).not.toContain('t-compare');
		expect(got).not.toContain('t-stitch');
		expect(got).toContain('t-edit');
		expect(got).toContain('t-any');
	});

	it('keeps multi-item tools visible (disabled by their own reason) on the bar', () => {
		const got = ids(manyCtx, 'bar');
		expect(got).toContain('t-compare');
		expect(got).toContain('t-stitch');
	});

	it('hides a single-item tool from a selection of several in the entry surface only', () => {
		expect(ids(manyCtx, 'entry')).not.toContain('t-edit');
		expect(ids(manyCtx, 'bar')).toContain('t-edit');
	});

	it('serves the bar and the selection menu from the same list, plus collection and delete actions', () => {
		const bar = ids(manyCtx, 'bar');
		const selection = ids(manyCtx, 'selection');
		expect(selection.filter((id) => bar.includes(id))).toEqual(bar);
		expect(selection).toEqual(expect.arrayContaining(['add-to-collection', 'copy-to-library', 'deselect', 'delete']));
		expect(bar).not.toContain('delete');
		expect(bar).not.toContain('open-details');
	});

	it('keeps entry-only tools out of the selection and bar surfaces', () => {
		for (const surface of ['selection', 'bar'] as const) {
			const got = ids(manyCtx, surface);
			expect(got).not.toContain('open-details');
			expect(got).not.toContain('favorite');
			expect(got).not.toContain('download');
		}
	});

	it('orders groups open, analyze, compose, export, organize, danger', () => {
		const order = groupToolsFor(manyCtx, { surface: 'selection', host: HOST }).map((group) => group.category.id);
		const sorted = [...order].sort(
			(a, b) => ['open', 'analyze', 'compose', 'export', 'organize', 'danger'].indexOf(a) - ['open', 'analyze', 'compose', 'export', 'organize', 'danger'].indexOf(b)
		);
		expect(order).toEqual(sorted);
		expect(order[order.length - 1]).toBe('danger');
	});

	it('drops a tool whose host capability is missing', () => {
		const got = groupToolsFor(entryCtx, { surface: 'entry', host: { open: vi.fn() } }).flatMap((group) =>
			group.tools.map(({ tool }) => tool.id)
		);
		expect(got).toContain('open-details');
		expect(got).not.toContain('delete');
		expect(got).not.toContain('favorite');
	});

	it('hides image-only tools for a video entry', () => {
		const video = generation('v', {
			files: [{ id: 2, file_path: 'out/v.mp4', file_type: 'video', is_final: true, created_at: 'x' }]
		} as never);
		const ctx = buildHistoryEntryContext(video, video.files[0], null);
		expect(ids(ctx, 'entry')).not.toContain('t-edit');
	});

	it('shows only open, reuse, copy error and delete for a failed entry', () => {
		const failed = generation('f', { status: 'failed', files: [], error_message: 'boom' });
		const ctx = buildHistoryEntryContext(failed, undefined, null);
		expect(ids(ctx, 'entry')).toEqual(['open-details', 'reuse-settings', 'copy-error', 'delete']);
	});

	it('keeps Tools as one disabled row for a running entry', () => {
		const running = generation('r', { status: 'running', files: [] });
		const ctx = buildHistoryEntryContext(running, undefined, null);
		const groups = groupToolsFor(ctx, { surface: 'entry', host: HOST });
		const pending = groups.flatMap((group) => group.tools).find(({ tool }) => tool.id === 'pending-tools');
		expect(pending?.availability).toEqual({ enabled: false, reason: 'when finished' });
		expect(ids(ctx, 'entry')).not.toContain('t-edit');
	});

	it('counts history selection by generations and library selection by items', () => {
		expect(selectionCount(manyCtx)).toBe(3);
		expect(selectionCount(entryCtx)).toBe(1);
	});
});

describe('plugin applies_to mapping', () => {
	function entry(applies_to?: PluginMediaToolEntry['applies_to']): PluginMediaToolEntry {
		return { id: 'p', plugin_id: 'pl', label: 'P', category: 'compose', component: 'P.js', applies_to };
	}

	it('maps min and max selection onto the registry selection field', () => {
		expect(pluginMediaToolFromManifest(entry({ min_selection: 2, max_selection: 4 })).selection).toEqual({
			min: 2,
			max: 4
		});
		expect(pluginMediaToolFromManifest(entry({ max_selection: 1 })).selection).toEqual({ max: 1 });
		expect(pluginMediaToolFromManifest(entry()).selection).toEqual({});
	});

	it('maps media_kinds onto kinds and leaves them undefined when absent', () => {
		expect(pluginMediaToolFromManifest(entry({ media_kinds: ['image'] })).kinds).toEqual(['image']);
		expect(pluginMediaToolFromManifest(entry({})).kinds).toBeUndefined();
	});

	it('a plugin with min_selection 2 is hidden in the entry menu and shown on the bar', () => {
		registerMediaTool(pluginMediaToolFromManifest({ ...entry({ min_selection: 2 }), id: 'plug-multi' }));
		registerMediaTool(pluginMediaToolFromManifest({ ...entry({ max_selection: 1 }), id: 'plug-single' }));
		const one = generation('a');
		const entryCtx = buildHistoryEntryContext(one, one.files[0], null);
		const many = buildHistoryToolContext([generation('a'), generation('b')], ['a', 'b'], null);
		expect(ids(entryCtx, 'entry')).toContain('plug-single');
		expect(ids(entryCtx, 'entry')).not.toContain('plug-multi');
		expect(ids(many, 'bar')).toContain('plug-multi');
	});
});
