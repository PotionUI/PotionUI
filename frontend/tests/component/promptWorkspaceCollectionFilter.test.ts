// @vitest-environment jsdom
//
// The Prompt Library's collection folder tree lives in a sibling component
// (PromptsSidebar), not inside PromptWorkspace, so the page wires them
// together through PromptWorkspace's exported setCollectionFilter - the same
// bind:this pattern the toolbar already uses for openComposer/openDuplicatesScan.
// This proves that call actually reaches api.listPrompts with collection_id,
// and that it stays out of unfiltered loads.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// PromptWorkspace pulls in ModelAssignmentModal, which transitively touches
// the real auth store's api.setOnAuthExpired and other unrelated api methods
// at import time - spread the real module and override only what this test
// drives, rather than reproducing its whole surface as a mock.
vi.mock('$lib/services/api/index', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/services/api/index')>();
	return {
		...actual,
		api: {
			...actual.api,
			listPrompts: vi.fn(),
			searchPrompts: vi.fn(),
			getModels: vi.fn(),
			listCollections: vi.fn(),
			listPromptImporters: vi.fn()
		}
	};
});

const { api } = await import('$lib/services/api/index');
const { default: PromptWorkspace } = await import(
	'../../src/routes/prompts/components/PromptWorkspace.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function mountWorkspace() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: PromptWorkspace as never,
		target,
		props: {}
	});
	return {
		component: component as unknown as {
			setCollectionFilter: (id: string | undefined) => Promise<void>;
		},
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountWorkspace> | undefined;

beforeEach(() => {
	vi.mocked(api.listPrompts).mockResolvedValue({
		success: true,
		data: { items: [], total: 0, limit: 100, offset: 0 }
	} as never);
	vi.mocked(api.getModels).mockResolvedValue({
		success: true,
		data: { models: [] }
	} as never);
	vi.mocked(api.listCollections).mockResolvedValue({
		success: true,
		data: { collections: [], total: 0 }
	} as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('prompt workspace collection filter', () => {
	it('loads prompts without a collection_id on mount', async () => {
		mounted = mountWorkspace();
		await settle();

		expect(api.listPrompts).toHaveBeenCalledWith(
			expect.objectContaining({ collection_id: undefined })
		);
	});

	it('threads the selected folder id into the next listPrompts call', async () => {
		mounted = mountWorkspace();
		await settle();
		vi.mocked(api.listPrompts).mockClear();

		await mounted.component.setCollectionFilter('col-1');
		await settle();

		expect(api.listPrompts).toHaveBeenCalledWith(
			expect.objectContaining({ collection_id: 'col-1' })
		);
	});

	it('clears the filter when the folder selection is cleared', async () => {
		mounted = mountWorkspace();
		await settle();
		await mounted.component.setCollectionFilter('col-1');
		await settle();
		vi.mocked(api.listPrompts).mockClear();

		await mounted.component.setCollectionFilter(undefined);
		await settle();

		expect(api.listPrompts).toHaveBeenCalledWith(
			expect.objectContaining({ collection_id: undefined })
		);
	});
});
