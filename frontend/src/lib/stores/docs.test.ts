import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockGetDocsTree = vi.fn();
const mockGetDocContent = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getDocsTree: (...args: unknown[]) => mockGetDocsTree(...args),
		getDocContent: (...args: unknown[]) => mockGetDocContent(...args)
	}
}));

import { buildDocNavigation, docsStore, findDocItem } from './docs';
import type { DocSection } from '$lib/types/api';

const sampleSections: DocSection[] = [
	{
		id: 'user',
		title: 'User Guide',
		items: [
			{
				id: 'getting-started',
				title: 'Getting Started',
				type: 'markdown',
				live_kind: null,
				source: 'repo',
				plugin_id: null,
				order: 10
			}
		]
	},
	{
		id: 'developer',
		title: 'Developer',
		items: [
			{
				id: 'hooks',
				title: 'Hooks Catalog',
				type: 'live',
				live_kind: 'hooks',
				source: 'repo',
				plugin_id: null,
				order: 10
			}
		]
	}
];

describe('stores/docs', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		docsStore.reset();
	});

	describe('loadTree', () => {
		it('populates sections on success', async () => {
			mockGetDocsTree.mockResolvedValue({ success: true, data: { sections: sampleSections } });

			await docsStore.loadTree();

			const state = get(docsStore);
			expect(state.loading).toBe(false);
			expect(state.error).toBeNull();
			expect(state.sections).toEqual(sampleSections);
		});

		it('sets error state non-fatally on API failure response', async () => {
			mockGetDocsTree.mockResolvedValue({ success: false, message: 'nope' });

			await docsStore.loadTree();

			const state = get(docsStore);
			expect(state.loading).toBe(false);
			expect(state.sections).toEqual([]);
			expect(state.error).toBe('nope');
		});

		it('sets error state non-fatally when the request throws', async () => {
			mockGetDocsTree.mockRejectedValue(new Error('network down'));

			await docsStore.loadTree();

			const state = get(docsStore);
			expect(state.loading).toBe(false);
			expect(state.sections).toEqual([]);
			expect(state.error).toBe('Failed to load documentation');
		});
	});

	describe('select', () => {
		it('updates selectedId', () => {
			docsStore.select('getting-started');
			expect(get(docsStore).selectedId).toBe('getting-started');

			docsStore.select(null);
			expect(get(docsStore).selectedId).toBeNull();
		});
	});

	describe('loadContent', () => {
		it('fetches and caches content by id', async () => {
			mockGetDocContent.mockResolvedValue({
				success: true,
				data: { id: 'getting-started', title: 'Getting Started', markdown: '# Hello' }
			});

			const result = await docsStore.loadContent('getting-started');

			expect(result).toEqual({
				markdown: '# Hello',
				title: 'Getting Started',
				meta: null,
				refs: null
			});
			expect(mockGetDocContent).toHaveBeenCalledTimes(1);
			expect(docsStore.getCachedContent('getting-started')).toEqual({
				markdown: '# Hello',
				title: 'Getting Started',
				meta: null,
				refs: null
			});
		});

		it('caches meta/refs for a typed technique/model doc', async () => {
			const meta = { title: 'APG', category_group: 'Quality', status: 'stable', families: [], authors: [] };
			const refs = { models: [{ family_key: 'wan22', title: 'Wan 2.2', doc_id: 'models/wan22' }] };
			mockGetDocContent.mockResolvedValue({
				success: true,
				data: { id: 'techniques/apg', title: 'APG', markdown: '# APG', meta, refs }
			});

			const result = await docsStore.loadContent('techniques/apg');

			expect(result).toEqual({ markdown: '# APG', title: 'APG', meta, refs });
			expect(docsStore.getCachedContent('techniques/apg')).toEqual({
				markdown: '# APG',
				title: 'APG',
				meta,
				refs
			});
		});

		it('serves subsequent requests from cache without refetching', async () => {
			mockGetDocContent.mockResolvedValue({
				success: true,
				data: { id: 'getting-started', title: 'Getting Started', markdown: '# Hello' }
			});

			await docsStore.loadContent('getting-started');
			await docsStore.loadContent('getting-started');

			expect(mockGetDocContent).toHaveBeenCalledTimes(1);
		});

		it('returns null and sets contentError on failure without caching', async () => {
			mockGetDocContent.mockResolvedValue({ success: false, error: 'not found' });

			const result = await docsStore.loadContent('missing');

			expect(result).toBeNull();
			expect(get(docsStore).contentError).toBe('not found');
			expect(docsStore.getCachedContent('missing')).toBeUndefined();
		});

		it('returns null and sets contentError when the request throws', async () => {
			mockGetDocContent.mockRejectedValue(new Error('boom'));

			const result = await docsStore.loadContent('missing');

			expect(result).toBeNull();
			expect(get(docsStore).contentLoading).toBe(false);
			expect(get(docsStore).contentError).toBe('Failed to load document');
		});
	});

	describe('reset', () => {
		it('clears state and content cache', async () => {
			mockGetDocContent.mockResolvedValue({
				success: true,
				data: { id: 'getting-started', title: 'Getting Started', markdown: '# Hello' }
			});
			await docsStore.loadContent('getting-started');
			docsStore.select('getting-started');

			docsStore.reset();

			expect(get(docsStore).selectedId).toBeNull();
			expect(get(docsStore).sections).toEqual([]);
			expect(docsStore.getCachedContent('getting-started')).toBeUndefined();
		});
	});
});

describe('findDocItem', () => {
	it('finds an item across sections by id', () => {
		expect(findDocItem(sampleSections, 'hooks')?.title).toBe('Hooks Catalog');
		expect(findDocItem(sampleSections, 'nope')).toBeNull();
	});
});

describe('buildDocNavigation', () => {
	const categorizedSections: DocSection[] = [
		{
			id: 'developer',
			title: 'Developer',
			items: [
				{
					id: 'dev/overview',
					title: 'Overview',
					type: 'markdown',
					live_kind: null,
					source: 'repo',
					plugin_id: null,
					order: 10
				},
				{
					id: 'dev/presets',
					title: 'Presets',
					type: 'markdown',
					live_kind: null,
					source: 'repo',
					plugin_id: null,
					order: 30,
					category: 'Presets / Models',
					category_order: 20
				},
				{
					id: 'dev/models',
					title: 'Model inference',
					type: 'markdown',
					live_kind: null,
					source: 'repo',
					plugin_id: null,
					order: 40,
					category: 'Presets / Models',
					category_order: 20
				}
			]
		}
	];

	it('groups categorized items while retaining flat items', () => {
		const [section] = buildDocNavigation(categorizedSections);

		expect(section.entries).toHaveLength(2);
		expect(section.entries[0]).toMatchObject({ kind: 'item', id: 'dev/overview' });
		expect(section.entries[1]).toMatchObject({
			kind: 'category',
			title: 'Presets / Models',
			items: [
				{ id: 'dev/presets', title: 'Presets' },
				{ id: 'dev/models', title: 'Model inference' }
			]
		});
	});

	it('retains matching ancestors and only matching children for an item query', () => {
		const [section] = buildDocNavigation(categorizedSections, 'inference');

		expect(section.title).toBe('Developer');
		expect(section.entries).toHaveLength(1);
		expect(section.entries[0]).toMatchObject({
			kind: 'category',
			title: 'Presets / Models',
			items: [{ id: 'dev/models' }]
		});
	});

	it('retains every child when the category itself matches', () => {
		const [section] = buildDocNavigation(categorizedSections, 'presets / models');

		expect(section.entries[0]).toMatchObject({
			kind: 'category',
			items: [{ id: 'dev/presets' }, { id: 'dev/models' }]
		});
	});

	it('returns no sections when nothing matches', () => {
		expect(buildDocNavigation(categorizedSections, 'no such documentation')).toEqual([]);
	});
});
