import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
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
const { default: PromptWorkspace } = await import('../../src/routes/prompts/components/PromptWorkspace.svelte');
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

function heading(target: HTMLElement): string {
	return target.querySelector('h2')?.textContent?.trim() || '';
}

function findButton(target: HTMLElement, text: string): HTMLButtonElement | undefined {
	return Array.from(target.querySelectorAll('button')).find((button) => (button.textContent || '').trim() === text) as
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

const createdPrompt = {
	id: 'p-new',
	name: null,
	display_name: 'a lone lighthouse',
	usage_hint: null,
	model_id: null,
	model_name: null,
	source_provider: null,
	segments: [{ id: 's1', type: 'content', content: 'a lone lighthouse', chips: {}, enabled: true }],
	flattened_text: 'a lone lighthouse',
	usage_count: 0,
	last_used_at: null
};

beforeEach(() => {
	page.update((current) => ({ ...current, url: new URL('http://localhost/prompts') }));
	vi.mocked(api.listPrompts).mockResolvedValue({
		success: true,
		data: { items: [], total: 0, limit: 100, offset: 0 }
	} as never);
	vi.mocked(api.getModels).mockResolvedValue({
		success: true,
		data: { models: [] }
	} as never);
	vi.mocked(api.getPrompt).mockResolvedValue({ success: true, data: createdPrompt } as never);
	vi.mocked(api.listCollections).mockResolvedValue({
		success: true,
		data: { collections: [], total: 0 }
	} as never);
	vi.mocked(api.listPromptImporters).mockResolvedValue({ success: true, data: [] } as never);
	vi.mocked(api.getPromptGenerations).mockResolvedValue({
		success: true,
		data: { items: [], total: 0 }
	} as never);
	vi.mocked(api.createPrompt).mockResolvedValue({
		success: true,
		data: createdPrompt
	} as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('prompt workspace create mode', () => {
	it('opens the same form as editing, empty, with Create disabled, and hides the header toolbar', async () => {
		mounted = mountWorkspace();
		await settle();
		expect(mounted.target.querySelector('input[type="search"]')).not.toBeNull();

		await mounted.component.startNewPrompt();
		await settle();

		expect(heading(mounted.target)).toBe('New prompt');
		const nameField = mounted.target.querySelector(
			'input[placeholder="Content preview is used when unnamed"]'
		) as HTMLInputElement | null;
		expect(nameField?.value).toBe('');
		expect(findButton(mounted.target, 'Create')?.disabled).toBe(true);
		expect(mounted.target.querySelector('input[type="search"]')).toBeNull();
	});

	it('enables Create once a segment carries real content', async () => {
		mounted = mountWorkspace();
		await settle();
		await mounted.component.startNewPrompt();
		await settle();

		typeIntoSegment(mounted.target, 'a lone lighthouse');
		await settle();

		expect(findButton(mounted.target, 'Create')?.disabled).toBe(false);
	});

	it("Create posts the edit form's body shape and lands on the created prompt", async () => {
		mounted = mountWorkspace();
		await settle();
		await mounted.component.startNewPrompt();
		await settle();

		typeIntoSegment(mounted.target, 'a lone lighthouse');
		await settle();

		findButton(mounted.target, 'Create')?.click();
		await settle();

		expect(api.createPrompt).toHaveBeenCalledWith(
			expect.objectContaining({
				name: null,
				usage_hint: null,
				model_id: null,
				segments: expect.arrayContaining([expect.objectContaining({ content: 'a lone lighthouse' })])
			})
		);
		expect(heading(mounted.target)).toBe('a lone lighthouse');
	});

	it('Cancel drops the draft and returns to the grid without calling the API', async () => {
		mounted = mountWorkspace();
		await settle();
		await mounted.component.startNewPrompt();
		await settle();

		findButton(mounted.target, 'Cancel')?.click();
		await settle();

		expect(api.createPrompt).not.toHaveBeenCalled();
		expect(mounted.target.textContent).toContain('No prompts yet');
		expect(mounted.target.querySelector('input[type="search"]')).not.toBeNull();
	});
});
