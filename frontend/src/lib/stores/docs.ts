import { logger } from '$lib/utils/logger';
import { writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import type { DocSection, DocItem, DocRefs, ModelMeta, TechniqueMeta } from '$lib/types/api';

export interface LoadedDocContent {
	markdown: string;
	title: string;
	meta?: TechniqueMeta | ModelMeta | null;
	refs?: DocRefs | null;
}

export interface DocsState {
	sections: DocSection[];
	selectedId: string | null;
	loading: boolean;
	error: string | null;
	contentLoading: boolean;
	contentError: string | null;
}

export interface DocNavigationItem {
	kind: 'item';
	id: string;
	order: number;
	firstIndex: number;
	item: DocItem;
}

export interface DocNavigationCategory {
	kind: 'category';
	id: string;
	title: string;
	order: number;
	firstIndex: number;
	items: DocItem[];
}

export type DocNavigationEntry = DocNavigationItem | DocNavigationCategory;

export interface DocNavigationSection {
	id: DocSection['id'];
	title: string;
	entries: DocNavigationEntry[];
}

const initialState: DocsState = {
	sections: [],
	selectedId: null,
	loading: false,
	error: null,
	contentLoading: false,
	contentError: null
};

/** Find a doc item by id across all sections of a tree. */
export function findDocItem(sections: DocSection[], id: string): DocItem | null {
	for (const section of sections) {
		const item = section.items.find((i) => i.id === id);
		if (item) return item;
	}
	return null;
}

/**
 * Derive the sidebar's grouped navigation from the deliberately flat API tree.
 *
 * Category metadata is additive: legacy and plugin items without a category
 * remain direct children of their section. A category occupies the position of
 * its first item unless an explicit category order is supplied.
 */
export function buildDocNavigation(
	sections: DocSection[],
	filterText = ''
): DocNavigationSection[] {
	const query = filterText.trim().toLocaleLowerCase();

	return sections
		.map((section) => {
			const categoryMap = new Map<string, DocNavigationCategory>();
			const entries: DocNavigationEntry[] = [];

			section.items.forEach((item, index) => {
				const categoryTitle = item.category?.trim();
				if (!categoryTitle) {
					entries.push({
						kind: 'item',
						id: item.id,
						order: item.order,
						firstIndex: index,
						item
					});
					return;
				}

				const categoryId = categoryTitle.toLocaleLowerCase();
				const explicitOrder = item.category_order;
				const entryOrder = Number.isFinite(explicitOrder) ? explicitOrder! : item.order;
				const existing = categoryMap.get(categoryId);

				if (existing) {
					existing.items.push(item);
					existing.order = Math.min(existing.order, entryOrder);
					return;
				}

				const category: DocNavigationCategory = {
					kind: 'category',
					id: categoryId,
					title: categoryTitle,
					order: entryOrder,
					firstIndex: index,
					items: [item]
				};
				categoryMap.set(categoryId, category);
				entries.push(category);
			});

			entries.sort((a, b) => a.order - b.order || a.firstIndex - b.firstIndex);

			if (!query) {
				return { id: section.id, title: section.title, entries };
			}

			const sectionMatches = section.title.toLocaleLowerCase().includes(query);
			const filteredEntries = entries.flatMap((entry): DocNavigationEntry[] => {
				if (entry.kind === 'item') {
					return sectionMatches || entry.item.title.toLocaleLowerCase().includes(query)
						? [entry]
						: [];
				}

				const categoryMatches = entry.title.toLocaleLowerCase().includes(query);
				const items =
					sectionMatches || categoryMatches
						? entry.items
						: entry.items.filter((item) => item.title.toLocaleLowerCase().includes(query));

				return items.length > 0 ? [{ ...entry, items }] : [];
			});

			return { id: section.id, title: section.title, entries: filteredEntries };
		})
		.filter((section) => !query || section.entries.length > 0);
}

function createDocsStore() {
	const { subscribe, update, set } = writable<DocsState>(initialState);

	// id -> markdown / title / (technique|model) meta+refs. Kept outside the
	// store so re-selecting a doc doesn't trigger a network round-trip
	// (mirrors the non-fatal, cache-first pattern used by fieldTypes.ts for
	// reference data). meta/refs are undefined for every doc that isn't a
	// typed technique/model doc -- callers must treat them as optional.
	const contentCache = new Map<string, string>();
	const titleCache = new Map<string, string>();
	const metaCache = new Map<string, TechniqueMeta | ModelMeta | null>();
	const refsCache = new Map<string, DocRefs | null>();

	// Circuit breaker: dedupe concurrent requests per id and hold off retrying
	// a failed id for a few seconds, so no caller can turn a down backend into
	// a request storm.
	const inFlight = new Map<string, Promise<LoadedDocContent | null>>();
	const failedAt = new Map<string, number>();
	const FAILURE_COOLDOWN_MS = 5000;

	return {
		subscribe,

		async loadTree() {
			update((s) => ({ ...s, loading: true, error: null }));
			try {
				const response = await api.getDocsTree();
				if (response.success && response.data) {
					update((s) => ({ ...s, sections: response.data!.sections, loading: false }));
				} else {
					update((s) => ({
						...s,
						loading: false,
						error: response.message || response.error || 'Failed to load documentation'
					}));
				}
			} catch (err) {
				logger.error('Failed to load docs tree:', err);
				update((s) => ({ ...s, loading: false, error: 'Failed to load documentation' }));
			}
		},

		select(id: string | null) {
			update((s) => ({ ...s, selectedId: id }));
		},

		getCachedContent(id: string): LoadedDocContent | undefined {
			const markdown = contentCache.get(id);
			if (markdown === undefined) return undefined;
			return {
				markdown,
				title: titleCache.get(id) || '',
				meta: metaCache.get(id) ?? null,
				refs: refsCache.get(id) ?? null
			};
		},

		async loadContent(id: string): Promise<LoadedDocContent | null> {
			const cached = this.getCachedContent(id);
			if (cached !== undefined) {
				return cached;
			}

			// Concurrent callers share one request.
			const pending = inFlight.get(id);
			if (pending) return pending;

			// Recently failed: don't hammer a down backend; caller sees the failure.
			const lastFailure = failedAt.get(id);
			if (lastFailure !== undefined && Date.now() - lastFailure < FAILURE_COOLDOWN_MS) {
				return null;
			}

			const request = this._fetchContent(id);
			inFlight.set(id, request);
			try {
				return await request;
			} finally {
				inFlight.delete(id);
			}
		},

		async _fetchContent(id: string): Promise<LoadedDocContent | null> {
			update((s) => ({ ...s, contentLoading: true, contentError: null }));
			try {
				const response = await api.getDocContent(id);
				if (response.success && response.data) {
					const { markdown, title, meta = null, refs = null } = response.data;
					contentCache.set(id, markdown);
					titleCache.set(id, title);
					metaCache.set(id, meta);
					refsCache.set(id, refs);
					failedAt.delete(id);
					update((s) => ({ ...s, contentLoading: false }));
					return { markdown, title, meta, refs };
				}
				failedAt.set(id, Date.now());
				update((s) => ({
					...s,
					contentLoading: false,
					contentError: response.message || response.error || 'Failed to load document'
				}));
				return null;
			} catch (err) {
				logger.error('Failed to load doc content:', err);
				failedAt.set(id, Date.now());
				update((s) => ({ ...s, contentLoading: false, contentError: 'Failed to load document' }));
				return null;
			}
		},

		reset() {
			set(initialState);
			contentCache.clear();
			titleCache.clear();
			metaCache.clear();
			refsCache.clear();
			inFlight.clear();
			failedAt.clear();
		}
	};
}

export const docsStore = createDocsStore();
