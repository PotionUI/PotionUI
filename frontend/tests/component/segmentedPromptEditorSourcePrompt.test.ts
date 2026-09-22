// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';

vi.mock('../../src/lib/utils/chipParser', () => ({
	hydrateSegments: async (segments: unknown[]) => segments
}));

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/services/api/index')>();
	return {
		...actual,
		api: {
			...actual.api,
			listPrompts: vi.fn(),
			listSegmentTemplates: vi.fn(),
			createPrompt: vi.fn()
		}
	};
});

const { api } = await import('$lib/services/api/index');
const { default: SegmentedPromptEditor } = await import('../../src/lib/components/SegmentedPromptEditor.svelte');
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

const libraryTemplate = {
	id: 't-lib',
	name: 'Portrait',
	description: null,
	tags: [],
	segments: [{ id: 's1', type: 'content', content: 'studio portrait', chips: {}, enabled: true }]
};

function mount(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: SegmentedPromptEditor as never,
		target,
		props: { segments: [], ...props }
	});
	return { target, component };
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function buttonByText(root: ParentNode, text: string): HTMLButtonElement | undefined {
	return Array.from(root.querySelectorAll('button')).find((button) => (button.textContent || '').trim() === text) as
		| HTMLButtonElement
		| undefined;
}

async function openMoreMenuItem(target: HTMLElement, label: string) {
	(target.querySelector('button[aria-label="More prompt actions"]') as HTMLButtonElement).click();
	await settle();
	const item = Array.from(target.querySelectorAll('[role="menuitem"]')).find((el) =>
		(el.textContent || '').includes(label)
	) as HTMLButtonElement | undefined;
	if (!item) throw new Error(`menu item ${label} not found`);
	item.click();
	await settle();
}

async function applyFromLibrary(mode: 'append' | 'replace') {
	(document.body.querySelector('[role="option"]') as HTMLButtonElement).click();
	await settle();
	const radio = document.body.querySelector(`input[name="segment-apply-mode"][value="${mode}"]`) as HTMLInputElement;
	radio.click();
	await settle();
	buttonByText(document.body, 'Apply')?.click();
	await settle();
}

beforeEach(() => {
	vi.mocked(api.listPrompts).mockResolvedValue({
		success: true,
		data: { items: [libraryPrompt], total: 1, limit: 100, offset: 0 }
	} as never);
	vi.mocked(api.listSegmentTemplates).mockResolvedValue({
		success: true,
		data: { templates: [libraryTemplate] }
	} as never);
	vi.mocked(api.createPrompt).mockResolvedValue({
		success: true,
		data: { ...libraryPrompt, id: 'p-new' }
	} as never);
});

afterEach(() => {
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('SegmentedPromptEditor reports the library prompt behind the composition', () => {
	it('applying a library prompt reports its id', async () => {
		const onSourcePromptChange = vi.fn();
		const editor = mount({ onSourcePromptChange });
		buttonByText(editor.target, 'Prompts')?.click();
		await settle();
		await applyFromLibrary('append');

		expect(onSourcePromptChange).toHaveBeenCalledWith('p-lib');
	});

	it('saving the composition as a new prompt reports the created id', async () => {
		const onSourcePromptChange = vi.fn();
		const editor = mount({
			onSourcePromptChange,
			segments: [{ id: 'a', type: 'content', content: 'a lighthouse keeper', chips: {}, enabled: true }]
		});
		await openMoreMenuItem(editor.target, 'Save as Prompt');
		buttonByText(document.body, 'Save Prompt')?.click();
		await settle();

		expect(api.createPrompt).toHaveBeenCalled();
		expect(onSourcePromptChange).toHaveBeenCalledWith('p-new');
	});

	it('replacing the composition with a template clears the link', async () => {
		const onSourcePromptChange = vi.fn();
		const editor = mount({ onSourcePromptChange });
		buttonByText(editor.target, 'Templates')?.click();
		await settle();
		await applyFromLibrary('replace');

		expect(onSourcePromptChange).toHaveBeenCalledWith(null);
	});

	it('appending a template keeps the link untouched', async () => {
		const onSourcePromptChange = vi.fn();
		const editor = mount({ onSourcePromptChange });
		buttonByText(editor.target, 'Templates')?.click();
		await settle();
		await applyFromLibrary('append');

		expect(onSourcePromptChange).not.toHaveBeenCalled();
	});
});
