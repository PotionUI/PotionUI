import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/services/api/mediaTools', () => ({ listMediaTools: vi.fn() }));

import { buildLibraryToolContext, unregisterMediaTools, type MediaToolItem } from './tools';
import { registerEntryTools } from './entryTools';
import { syncCatalogTools } from './catalogTools';
import { buildRowContext, buildRowModel, type RowTool } from './rowModel';
import type { LibraryItem } from '$lib/services/api/library';
import type { GenerationHistoryItem } from '$lib/types/history';
import { A1111_TOOL, BOOMERANG_TOOL, CORE_TOOLS, FIX_COLORS_TOOL } from '../../../tests/component/fixtures/mediaToolCatalog';

registerEntryTools();

function item(kind: MediaToolItem['kind'], extra: Partial<MediaToolItem> = {}): MediaToolItem {
	return { id: 'i1', kind, url: `/u/i1.${kind}`, filename: `clip.${kind === 'image' ? 'png' : 'bin'}`, ...extra };
}

function ids(entries: RowTool[]): string[] {
	return entries.map((entry) => entry.id);
}

function historyContext(kind: MediaToolItem['kind']) {
	const generation = { id: 'g', files: [], status: 'completed' } as unknown as GenerationHistoryItem;
	return {
		...buildRowContext('history', [item(kind, { generationId: 'g' })]),
		generations: [generation],
		generationIds: ['g']
	};
}

beforeEach(() => {
	unregisterMediaTools((tool) => !!tool.catalogToolId);
	syncCatalogTools([...CORE_TOOLS, A1111_TOOL, BOOMERANG_TOOL]);
});

describe('buildRowModel', () => {
	it('lists the core Edit tools of a video in catalog order, then plugin tools last', () => {
		const model = buildRowModel(buildRowContext('library', [item('video')]));
		expect(ids(model.edit)).toEqual(['trim', 'crop', 'resize', 'rotate', 'flip', 'speed', 'frame', 'boomerang.loop']);
		expect(model.edit.map((entry) => entry.plugin ?? false)).toEqual([
			false, false, false, false, false, false, false, true
		]);
	});

	it('adds Edit image after the core tools for an image', () => {
		const model = buildRowModel(buildRowContext('library', [item('image')]));
		expect(ids(model.edit)).toEqual([
			'crop',
			'resize',
			'rotate',
			'flip',
			'fit_canvas',
			'color_key',
			'crop_subject',
			'edit-image'
		]);
	});

	it('offers audio trim, speed and split', () => {
		const model = buildRowModel(buildRowContext('library', [item('audio')]));
		expect(ids(model.edit)).toEqual(['trim', 'speed', 'split']);
	});

	it('shows Effects for an image only', () => {
		expect(ids(buildRowModel(buildRowContext('library', [item('image')])).effects)).toEqual(['apply-look']);
		expect(buildRowModel(buildRowContext('library', [item('video')])).effects).toEqual([]);
		expect(buildRowModel(buildRowContext('library', [item('audio')])).effects).toEqual([]);
	});

	it('hides a tool that needs more than one item instead of showing it disabled', () => {
		const model = buildRowModel(buildRowContext('library', [item('video')]));
		expect(ids(model.edit)).not.toContain('concat');
	});

	it('keeps a tool the item cannot take visible, disabled, with its reason', () => {
		const model = buildRowModel(buildRowContext('library', [item('image', { filename: 'photo.jpg' })]));
		const subject = model.edit.find((entry) => entry.id === 'crop_subject');
		expect(subject).toMatchObject({ enabled: false, reason: 'Needs a transparent image' });
	});

	it('enables Crop to subject for an image known to have transparency, disables it for one known to be opaque', () => {
		const transparent = buildRowModel(buildRowContext('library', [item('image', { hasAlpha: true })]));
		const opaque = buildRowModel(buildRowContext('library', [item('image', { hasAlpha: false })]));
		const unknown = buildRowModel(buildRowContext('library', [item('image', { hasAlpha: null })]));
		expect(transparent.edit.find((entry) => entry.id === 'crop_subject')?.enabled).toBe(true);
		expect(opaque.edit.find((entry) => entry.id === 'crop_subject')).toMatchObject({
			enabled: false,
			reason: 'Needs a transparent image'
		});
		expect(unknown.edit.find((entry) => entry.id === 'crop_subject')?.enabled).toBe(true);
	});

	it('puts the A1111 export in downloads with its plugin id, only on History', () => {
		const history = buildRowModel(historyContext('image'));
		expect(ids(history.downloads)).toEqual([A1111_TOOL.id]);
		expect(history.downloads[0]).toMatchObject({ plugin: true, pluginId: A1111_TOOL.source });
		expect(ids([...history.edit, ...history.effects, ...history.plugins])).not.toContain(A1111_TOOL.id);
		expect(buildRowModel(buildRowContext('library', [item('image')])).downloads).toEqual([]);
	});

	it('leaves a plugin tool out of downloads when the media is not one it accepts', () => {
		expect(buildRowModel(historyContext('video')).downloads).toEqual([]);
	});

	it('gives both pages the same Edit and Effects for the same kind of media', () => {
		for (const kind of ['image', 'video', 'audio'] as const) {
			const library = buildRowModel(buildRowContext('library', [item(kind)]));
			const history = buildRowModel(buildRowContext('history', [item(kind, { generationId: 'g' })]));
			expect(ids(history.edit)).toEqual(ids(library.edit));
			expect(ids(history.effects)).toEqual(ids(library.effects));
		}
	});

	it('is empty for media nothing edits', () => {
		const model = buildRowModel(buildRowContext('history', [item('mesh')]));
		expect(model).toEqual({ edit: [], effects: [], downloads: [], plugins: [] });
	});

	it('carries the note a tool shows under its label', () => {
		const model = buildRowModel(buildRowContext('library', [item('video')]));
		expect(model.edit.find((entry) => entry.id === 'frame')?.note).toBe('Saves one image');
	});
});

describe('catalog effects in the Effects row', () => {
	beforeEach(() => {
		syncCatalogTools([...CORE_TOOLS, A1111_TOOL, BOOMERANG_TOOL, FIX_COLORS_TOOL]);
	});

	it('shows Fix colours for a video and keeps it out of Edit', () => {
		const model = buildRowModel(buildRowContext('library', [item('video')]));

		expect(ids(model.effects)).toEqual(['fix_colors']);
		expect(ids(model.edit)).not.toContain('fix_colors');
		expect(model.effects[0]).toMatchObject({ action: 'catalog', label: 'Fix colours', enabled: true });
	});

	it('puts it ahead of Apply a look for an image-only tool set, and hides it for an image', () => {
		const model = buildRowModel(buildRowContext('library', [item('image')]));

		expect(ids(model.effects)).toEqual(['apply-look']);
	});

	it('offers it on History too', () => {
		expect(ids(buildRowModel(historyContext('video')).effects)).toEqual(['fix_colors']);
	});
});

describe('recent looks in the Effects row', () => {
	const looks = [
		{ id: 'noir', name: 'Noir', group: 'Black and white' },
		{ id: 'retro-tape:vhs', name: 'VHS', group: 'Retro Tape' }
	];

	it('follow Apply a look, one button each, in the order given', () => {
		const model = buildRowModel(buildRowContext('library', [item('image')]), looks);

		expect(ids(model.effects)).toEqual(['apply-look', 'look:noir', 'look:retro-tape:vhs']);
		expect(model.effects.map((entry) => entry.label)).toEqual(['Apply a look…', 'Noir', 'VHS']);
	});

	it('carry the look id and group, which Apply a look does not', () => {
		const [open, noir] = buildRowModel(buildRowContext('library', [item('image')]), looks).effects;

		expect(open.lookId).toBeUndefined();
		expect(noir).toMatchObject({ lookId: 'noir', note: 'Black and white', action: 'apply-look' });
	});

	it('are left out for video and audio along with the rest of Effects', () => {
		expect(buildRowModel(buildRowContext('library', [item('video')]), looks).effects).toEqual([]);
		expect(buildRowModel(buildRowContext('library', [item('audio')]), looks).effects).toEqual([]);
	});

	it('show on both pages alike', () => {
		const library = buildRowModel(buildRowContext('library', [item('image')]), looks);
		const history = buildRowModel(buildRowContext('history', [item('image', { generationId: 'g' })]), looks);

		expect(ids(history.effects)).toEqual(ids(library.effects));
	});

	it('are absent when no look was applied yet', () => {
		expect(ids(buildRowModel(buildRowContext('library', [item('image')])).effects)).toEqual(['apply-look']);
	});
});

describe('buildRowContext', () => {
	it('builds the same context a card entry does, so hidden rules agree', () => {
		const ctx = buildRowContext('library', [item('video')]);
		expect(ctx.scope).toBe('library');
		expect([...ctx.kinds]).toEqual(['video']);
		expect(ctx.entry?.status).toBe('completed');
	});

	it('agrees with the library context on the items it is given', () => {
		const row = { id: 'u1', filename: 'u1.png', media_type: 'image', url: '/u/u1.png', tags: [] } as LibraryItem;
		const ctx = buildLibraryToolContext([row], null);
		expect(buildRowContext('library', ctx.items).items).toEqual(ctx.items);
	});
});
