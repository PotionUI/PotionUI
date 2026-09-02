// @vitest-environment jsdom
//
// "New prompt" used to open a separate composer modal with its own plain
// textarea; now it puts the workspace's detail pane into a create mode that
// reuses the exact same form as editing. This proves the pane actually
// switches into that mode, gates Create on real segment content, posts the
// same body shape the edit form does, and lands on the created prompt
// afterward - and that Cancel throws the draft away without touching the API.
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
			listPromptImporters: vi.fn(),
			createPrompt: vi.fn(),
			getPromptGenerations: vi.fn()
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
	return Array.from(target.querySelectorAll('button')).find(
		(button) => (button.textContent || '').trim() === text
	) as HTMLButtonElement | undefined;
}

/** Simulates typing plain text into a segment's contenteditable body, the
 *  way InlineChipEditor.svelte's own handleInput reads it back (see
 *  chipEditorDom.test.ts's `extractContentFromDOM` coverage). */
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
	it('opens the same form as editing, empty, with Create disabled', async () => {
		mounted = mountWorkspace();
		await settle();

		await mounted.component.startNewPrompt();
		await settle();

		expect(heading(mounted.target)).toBe('New prompt');
		const nameField = mounted.target.querySelector(
			'input[placeholder="Content preview is used when unnamed"]'
		) as HTMLInputElement | null;
		expect(nameField?.value).toBe('');
		expect(findButton(mounted.target, 'Create')?.disabled).toBe(true);
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

	it('Create posts the edit form\'s body shape and selects the new prompt', async () => {
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
		expect(heading(mounted.target)).toBe('Edit Prompt');
	});

	it('Cancel discards the draft and returns to the empty state without calling the API', async () => {
		mounted = mountWorkspace();
		await settle();
		await mounted.component.startNewPrompt();
		await settle();

		typeIntoSegment(mounted.target, 'a lone lighthouse');
		await settle();

		findButton(mounted.target, 'Cancel')?.click();
		await settle();

		expect(api.createPrompt).not.toHaveBeenCalled();
		expect(mounted.target.textContent).toContain('No prompt selected');
	});
});
