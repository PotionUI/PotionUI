import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import type { Writable } from 'svelte/store';

type PageStore = Writable<{ url: URL }>;

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/services/api/index')>();
	return {
		...actual,
		api: {
			...actual.api,
			listPrompts: vi.fn(),
			searchPrompts: vi.fn(),
			getModels: vi.fn(),
			getPrompt: vi.fn(),
			listCollections: vi.fn(),
			listPromptImporters: vi.fn(),
			createPrompt: vi.fn(),
			getPromptGenerations: vi.fn()
		}
	};
});

vi.mock('$app/navigation', async () => {
	const { page } = await import('$app/stores');
	const store = page as unknown as PageStore;
	return {
		goto: async (href: string) => {
			store.update((current) => ({ ...current, url: new URL(href, 'http://localhost') }));
		},
		afterNavigate: () => {},
		beforeNavigate: () => {}
	};
});

const { api } = await import('$lib/services/api/index');
const page = (await import('$app/stores')).page as unknown as PageStore;
const { tabsStore, activeTab } = await import('$lib/stores/tabs');
const { default: PromptWorkspace } = await import('../../src/routes/prompts/components/PromptWorkspace.svelte');
const { createClassComponent } = await import('svelte/legacy');

const libraryPrompt = {
	id: 'p-lib',
	name: 'Lighthouse',
	display_name: 'Lighthouse',
	usage_hint: null,
	model_id: null,
	model_name: null,
	source_provider: null,
	segments: [{ id: 's1', type: 'content', content: 'a lone lighthouse', chips: {}, enabled: true }],
	flattened_text: 'a lone lighthouse',
	usage_count: 0,
	last_used_at: null
};

function mountWorkspace() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: PromptWorkspace as never, target, props: {} });
	return {
		component: component as unknown as { startNewPrompt: () => Promise<void> },
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function findButton(root: ParentNode, text: string): HTMLButtonElement | undefined {
	return Array.from(root.querySelectorAll('button')).find((button) => (button.textContent || '').trim() === text) as
		| HTMLButtonElement
		| undefined;
}

function typeIntoSegment(target: HTMLElement, text: string) {
	const editor = target.querySelector('.inline-chip-editor[role="textbox"]');
	if (!editor) throw new Error('segment editor not found');
	editor.textContent = text;
	editor.dispatchEvent(new Event('input', { bubbles: true }));
}

let mounted: ReturnType<typeof mountWorkspace> | undefined;

beforeEach(() => {
	tabsStore.reset();
	page.update((current) => ({ ...current, url: new URL('http://localhost/prompts') }));
	vi.mocked(api.listPrompts).mockResolvedValue({
		success: true,
		data: { items: [libraryPrompt], total: 1, limit: 100, offset: 0 }
	} as never);
	vi.mocked(api.getModels).mockResolvedValue({ success: true, data: { models: [] } } as never);
	vi.mocked(api.getPrompt).mockResolvedValue({ success: true, data: libraryPrompt } as never);
	vi.mocked(api.listCollections).mockResolvedValue({ success: true, data: { collections: [], total: 0 } } as never);
	vi.mocked(api.listPromptImporters).mockResolvedValue({ success: true, data: [] } as never);
	vi.mocked(api.getPromptGenerations).mockResolvedValue({ success: true, data: { items: [], total: 0 } } as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('prompt workspace Use links the active tab to the library prompt', () => {
	it("the card's Use sets sourcePromptId on the active generate tab", async () => {
		mounted = mountWorkspace();
		await settle();
		expect(get(activeTab)?.sourcePromptId ?? null).toBeNull();

		findButton(mounted.target, 'Use')?.click();
		await settle();

		const tab = get(activeTab);
		expect(tab?.sourcePromptId).toBe('p-lib');
		expect(tab?.promptSegments?.map((segment) => segment.content)).toEqual(['a lone lighthouse']);
	});

	it('Use in Generate from the editor links the prompt being edited', async () => {
		page.update((current) => ({ ...current, url: new URL('http://localhost/prompts?id=p-lib') }));
		mounted = mountWorkspace();
		await settle();

		const useButton = findButton(mounted.target, 'Use in Generate');
		expect(useButton).toBeDefined();
		useButton?.click();
		await settle();

		expect(get(activeTab)?.sourcePromptId).toBe('p-lib');
	});

	it('Use in Generate from an unsaved new prompt leaves the tab unlinked', async () => {
		tabsStore.updateTab(get(activeTab)!.id, { sourcePromptId: 'p-stale' });
		mounted = mountWorkspace();
		await settle();
		await mounted.component.startNewPrompt();
		await settle();
		typeIntoSegment(mounted.target, 'a fresh draft');
		await settle();

		findButton(mounted.target, 'Use in Generate')?.click();
		await settle();

		expect(get(activeTab)?.sourcePromptId).toBeNull();
	});
});
