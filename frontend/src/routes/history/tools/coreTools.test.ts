import { describe, expect, it, vi } from 'vitest';
import { buildHistoryToolContext, listHistoryToolGroups } from '$lib/history/tools';
import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';

// The modals are leaves here: only the registrations and their `applies()`
// predicates are under test, and mounting one would drag the whole modal stack
// into a node-environment run.
vi.mock('../components/HistoryCompareModal.svelte', () => ({ default: {} }));
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
	return listHistoryToolGroups(buildHistoryToolContext(ids.map(generation), ids, null));
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
});
