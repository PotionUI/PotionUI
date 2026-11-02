import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

vi.mock('../components/HistoryApplyLookModal.svelte', () => ({ default: {} }));
vi.mock('../components/HistoryCompareModal.svelte', () => ({ default: {} }));
vi.mock('../components/HistoryEditImageModal.svelte', () => ({ default: {} }));
vi.mock('../components/HistoryExportZipModal.svelte', () => ({ default: {} }));
vi.mock('../components/HistoryStitchModal.svelte', () => ({ default: {} }));
vi.mock('$app/environment', () => ({ browser: true }));

const { recentLookToolId, syncRecentLookTools } = await import('./recentLookTools');
const { registerCoreHistoryTools } = await import('./coreTools');
const { recentLooks, recordRecentLook } = await import('$lib/filters/recentLooks');
const { mediaToolRegistrations } = await import('$lib/tools/pluginTools');
const { buildLibraryToolContext, listToolGroups, listTools } = await import('$lib/tools/tools');

const look = (id: string, group = 'Colour') => ({ id, name: id.toUpperCase(), group });
const image = (id: string) =>
	({ id, filename: `${id}.png`, media_type: 'image', url: `/u/${id}.png`, tags: [] }) as never;

function effectsFor(items: never[]) {
	const group = listToolGroups(buildLibraryToolContext(items, null)).find((entry) => entry.category.id === 'effects');
	return (
		group?.tools
			.filter(({ availability }) => !availability.hidden)
			.map(({ tool, availability }) => ({ id: tool.id, availability, tool })) ?? []
	);
}

beforeEach(() => {
	recentLooks.set([]);
	registerCoreHistoryTools();
});

describe('recent look tools', () => {
	it('puts each recent look under Effects after Apply a look', () => {
		syncRecentLookTools([look('noir'), look('ember')]);

		expect(effectsFor([image('a')]).map((entry) => entry.id)).toEqual(['apply-look', 'look:noir', 'look:ember']);
	});

	it('names the tool for the look and files it under the look group', () => {
		syncRecentLookTools([look('noir', 'Black and white')]);

		const [, noir] = effectsFor([image('a')]);
		expect(noir.tool.label).toBe('NOIR');
		expect(noir.tool.note).toBe('Black and white');
		expect(noir.tool.lookId).toBe('noir');
	});

	it('is offered on both pages and for images only', () => {
		syncRecentLookTools([look('noir')]);
		const tool = listTools().find((entry) => entry.id === recentLookToolId('noir'))!;

		expect(tool.scopes).toEqual(['history', 'library']);
		expect(tool.kinds).toEqual(['image']);
	});

	it('hides them for a video, an audio file and a mixed selection', () => {
		syncRecentLookTools([look('noir')]);
		const item = (id: string, kind: string) =>
			({ id, filename: id, media_type: kind, url: `/u/${id}`, tags: [] }) as never;

		expect(effectsFor([item('v', 'video')])).toEqual([]);
		expect(effectsFor([item('s', 'audio')])).toEqual([]);
		expect(effectsFor([image('a'), item('v', 'video')])).toEqual([]);
	});

	it('stay enabled when several images are selected', () => {
		syncRecentLookTools([look('noir')]);

		const rows = effectsFor([image('a'), image('b')]);
		expect(rows.map((row) => row.availability.enabled)).toEqual([true, true]);
	});

	it('replaces the previous list instead of adding to it', () => {
		syncRecentLookTools([look('a'), look('b'), look('c')]);
		syncRecentLookTools([look('d')]);

		expect(listTools().filter((tool) => tool.lookId).map((tool) => tool.id)).toEqual(['look:d']);
	});

	it('drops them all when there are none', () => {
		syncRecentLookTools([look('a')]);
		syncRecentLookTools([]);

		expect(listTools().filter((tool) => tool.lookId)).toEqual([]);
		expect(effectsFor([image('a')]).map((entry) => entry.id)).toEqual(['apply-look']);
	});

	it('follows the recent looks store, so a look applied in the editor shows up in the menu', () => {
		recordRecentLook(look('noir'));
		recordRecentLook(look('ember'));

		expect(effectsFor([image('a')]).map((entry) => entry.id)).toEqual(['apply-look', 'look:ember', 'look:noir']);
	});

	it('shows at most the three the store keeps', () => {
		for (const id of ['a', 'b', 'c', 'd']) recordRecentLook(look(id));

		expect(effectsFor([image('a')]).map((entry) => entry.id)).toEqual([
			'apply-look',
			'look:d',
			'look:c',
			'look:b'
		]);
	});

	it('tells an open menu to rebuild when the list changes', () => {
		const before = get(mediaToolRegistrations);

		syncRecentLookTools([look('a')]);

		expect(get(mediaToolRegistrations)).toBeGreaterThan(before);
	});
});
