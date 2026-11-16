import { describe, expect, it, vi } from 'vitest';
import { buildHistoryToolContext, buildLibraryToolContext, listToolGroups } from '$lib/tools/tools';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';

// The modals are leaves here: only the registrations and their `applies()`
// predicates are under test, and mounting one would drag the whole modal stack
// into a node-environment run.
vi.mock('../components/HistoryApplyLookModal.svelte', () => ({ default: {} }));
vi.mock('../components/HistoryCompareModal.svelte', () => ({ default: {} }));
vi.mock('../components/HistoryEditImageModal.svelte', () => ({ default: {} }));
vi.mock('../components/HistoryExportZipModal.svelte', () => ({ default: {} }));
vi.mock('../components/HistoryStitchModal.svelte', () => ({ default: {} }));

const { registerCoreHistoryTools } = await import('./coreTools');

registerCoreHistoryTools();

function generation(id: string): GenerationHistoryItem {
	const image: GenerationFile = {
		id: 1,
		file_path: `out/${id}.png`,
		file_type: 'IMAGE',
		is_final: true,
		created_at: '2026-09-09T00:00:00Z'
	};
	return {
		id,
		form_data: {},
		status: 'completed',
		progress: 100,
		created_at: '2026-09-09T00:00:00Z',
		updated_at: '2026-09-09T00:00:00Z',
		files: [image],
		rating: 0,
		is_favorite: false
	};
}

function groupsFor(ids: string[]) {
	return listToolGroups(buildHistoryToolContext(ids.map(generation), ids, null));
}

function entryFor(toolId: string, ids: string[]) {
	const entry = groupsFor(ids)
		.flatMap((group) => group.tools.map((item) => ({ ...item, category: group.category })))
		.find(({ tool }) => tool.id === toolId);
	if (!entry) throw new Error(`${toolId} is not registered`);
	return entry;
}

describe('core history tools', () => {
	it('files compare under Analyze and the zip export under Export', () => {
		expect(entryFor('compare', ['a', 'b']).category.id).toBe('analyze');
		expect(entryFor('export-zip', ['a', 'b']).category.id).toBe('export');
		expect(entryFor('export-zip', ['a', 'b']).tool.label).toBe('Download .zip');
	});

	it('offers compare only for exactly two generations', () => {
		expect(entryFor('compare', ['a']).availability).toEqual({
			enabled: false,
			reason: 'Select exactly 2 generations'
		});
		expect(entryFor('compare', ['a', 'b']).availability).toEqual({ enabled: true });
		expect(entryFor('compare', ['a', 'b', 'c']).availability).toEqual({
			enabled: false,
			reason: 'Select exactly 2 generations'
		});
	});

	it('offers the zip export for any non-empty selection', () => {
		expect(entryFor('export-zip', ['a']).availability).toEqual({ enabled: true });
		expect(entryFor('export-zip', ['a', 'b', 'c']).availability).toEqual({ enabled: true });
	});

	it('files edit image under Compose on both pages', () => {
		const history = entryFor('edit-image', ['a']);
		expect(history.category.id).toBe('compose');
		expect(history.tool.scopes).toEqual(['history', 'library']);
		expect(history.tool.label).toBe('Open in editor');
	});

	it('offers the editor for one or many items on the history page', () => {
		expect(entryFor('edit-image', ['a']).availability).toEqual({ enabled: true });
		expect(entryFor('edit-image', ['a', 'b']).availability).toEqual({ enabled: true });
		expect(entryFor('edit-image', []).availability).toEqual({
			enabled: false,
			reason: 'Select an image or a video'
		});
	});

	it('offers the editor for images and videos on the library page, not for audio', () => {
		const item = (id: string, media_type: string) =>
			({ id, filename: `${id}.png`, media_type, url: `/u/${id}.png` }) as never;
		const entry = (items: never[]) =>
			listToolGroups(buildLibraryToolContext(items, null))
				.flatMap((group) => group.tools)
				.find(({ tool }) => tool.id === 'edit-image');

		expect(entry([item('a', 'image')])?.availability).toEqual({ enabled: true });
		expect(entry([item('a', 'image'), item('b', 'image')])?.availability.enabled).toBe(true);
		expect(entry([item('v', 'video')])?.availability).toEqual({ enabled: true });
		expect(entry([item('s', 'audio')])?.availability).toEqual({
			enabled: false,
			reason: 'Only images and videos can be edited'
		});
	});

	it('files Apply a look under Effects on both pages', () => {
		const entry = entryFor('apply-look', ['a']);
		expect(entry.category.id).toBe('effects');
		expect(entry.tool.scopes).toEqual(['history', 'library']);
		expect(entry.tool.label).toBe('Apply a look…');
	});

	it('offers a look for one or many images', () => {
		expect(entryFor('apply-look', ['a']).availability).toEqual({ enabled: true });
		expect(entryFor('apply-look', ['a', 'b']).availability).toEqual({ enabled: true });
	});

	it('hides Apply a look for video, audio and an empty selection', () => {
		const item = (id: string, media_type: string) =>
			({ id, filename: `${id}.bin`, media_type, url: `/u/${id}` }) as never;
		const availability = (items: never[]) =>
			listToolGroups(buildLibraryToolContext(items, null))
				.flatMap((group) => group.tools)
				.find(({ tool }) => tool.id === 'apply-look')?.availability;

		expect(availability([item('v', 'video')])?.hidden).toBe(true);
		expect(availability([item('s', 'audio')])?.hidden).toBe(true);
		expect(availability([item('a', 'image'), item('v', 'video')])?.hidden).toBe(true);
		expect(availability([])?.hidden).toBe(true);
		expect(availability([item('a', 'image')])?.hidden).toBeUndefined();
	});
});

describe('Continue this edit in the item and Tools menus', () => {
	const link = { edit_id: 'ed_1', tool: 'image', revision: 2, available: true, continuable: true };
	const row = (id: string, extra: Record<string, unknown> = {}) =>
		({ id, filename: `${id}.png`, media_type: 'image', url: `/${id}.png`, tags: [], ...extra }) as never;

	function libraryEdit(rows: unknown[]) {
		const groups = listToolGroups(buildLibraryToolContext(rows as never, null));
		const entries = groups.flatMap((group) => group.tools);
		const visible = entries.filter(({ availability }) => !availability.hidden).map(({ tool }) => tool.id);
		return {
			rows: visible.filter((id) => id === 'continue-edit' || id === 'edit-image'),
			continue: entries.find(({ tool }) => tool.id === 'continue-edit')
		};
	}

	it('lists Continue this edit before Edit for one linked, available, continuable picture', () => {
		const result = libraryEdit([row('p1', { made_in_editor: link })]);
		expect(result.rows).toEqual(['continue-edit', 'edit-image']);
		expect(result.continue?.availability.enabled).toBe(true);
		expect(result.continue?.tool.label).toBe('Continue this edit');
	});

	it('keeps the single Edit row when the edit is gone, was replaced, or there is no link', () => {
		expect(libraryEdit([row('p1', { made_in_editor: { ...link, available: false } })]).rows).toEqual(['edit-image']);
		expect(libraryEdit([row('p1', { made_in_editor: { ...link, continuable: false } })]).rows).toEqual(['edit-image']);
		expect(libraryEdit([row('p1')]).rows).toEqual(['edit-image']);
	});

	it('keeps the single Edit row when several pictures are selected', () => {
		expect(libraryEdit([row('p1', { made_in_editor: link }), row('p2', { made_in_editor: link })]).rows).toEqual(['edit-image']);
	});

	it('never offers it in History', () => {
		const ids = groupsFor(['g1']).flatMap((group) => group.tools.map(({ tool }) => tool.id));
		expect(ids).not.toContain('continue-edit');
		expect(ids).toContain('edit-image');
	});
});
