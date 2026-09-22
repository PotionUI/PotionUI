import { describe, it, expect, afterEach, vi } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }) })),
		getGenerationImageURL: vi.fn(() => ''),
		getGenerationThumbnailURL: vi.fn(() => ''),
		getModelById: vi.fn().mockResolvedValue({ data: { model: null } }),
		setOnAuthExpired: vi.fn()
	}
}));

vi.mock('../../src/lib/utils/chipParser', () => ({
	hydrateSegments: async (segments: unknown[]) => segments
}));

const { default: PromptDetailView } = await import(
	'../../src/routes/prompts/components/PromptDetailView.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | null = null;

function mountDetail(collections: Array<{ id: string; name: string }> | undefined) {
	target = document.createElement('div');
	document.body.appendChild(target);
	const onRemoveFromCollection = vi.fn().mockResolvedValue(true);
	component = createClassComponent({
		component: PromptDetailView as never,
		target,
		props: {
			mode: 'edit',
			prompt: { id: 'prompt-1', display_name: 'Dancing girl factory', usage_count: 0, collections },
			name: 'Dancing girl factory',
			usageHint: '',
			editModelId: null,
			editModelLabel: null,
			editorSegments: [{ id: 'a', content: 'a lighthouse keeper', type: 'content', chips: {}, enabled: true }],
			editorVariables: {},
			usageItems: [],
			usageTotal: 0,
			usageLoading: false,
			saving: false,
			dirtyCount: 0,
			promptCollections: [],
			onBack: vi.fn(),
			onSave: vi.fn(),
			onDiscard: vi.fn(),
			onDelete: vi.fn(),
			onDuplicate: vi.fn(),
			onUse: vi.fn(),
			onAddToCollection: vi.fn(),
			onCreateAndAddToCollection: vi.fn(),
			onRemoveFromCollection,
			onOpenVariableManager: vi.fn(),
			onVariableDefChange: vi.fn()
		}
	});
	return { el: target, onRemoveFromCollection };
}

function collectionsSection(el: HTMLElement): HTMLElement {
	const heading = Array.from(el.querySelectorAll('h3')).find((h) => h.textContent?.trim() === 'Collections');
	if (!heading) throw new Error('Collections section not found');
	return heading.closest('section') as HTMLElement;
}

afterEach(() => {
	component?.$destroy();
	component = null;
	target?.remove();
});

describe('PromptDetailView collections section', () => {
	it('renders one chip per collection the prompt belongs to', async () => {
		const { el } = mountDetail([
			{ id: 'col-1', name: 'Portraits' },
			{ id: 'col-2', name: 'Studio' }
		]);
		await tick();

		const chips = collectionsSection(el).querySelectorAll('[data-testid="prompt-collection-chips"] > li');
		expect(Array.from(chips).map((chip) => chip.textContent?.trim())).toEqual(['Portraits', 'Studio']);
		expect(collectionsSection(el).textContent).not.toContain('Not in any collection');
	});

	it('removes a membership through the callback with that collection id', async () => {
		const { el, onRemoveFromCollection } = mountDetail([
			{ id: 'col-1', name: 'Portraits' },
			{ id: 'col-2', name: 'Studio' }
		]);
		await tick();

		const remove = collectionsSection(el).querySelector(
			'button[aria-label="Remove from Studio"]'
		) as HTMLButtonElement;
		expect(remove).toBeTruthy();
		remove.click();
		await tick();

		expect(onRemoveFromCollection).toHaveBeenCalledTimes(1);
		expect(onRemoveFromCollection).toHaveBeenCalledWith('col-2');
	});

	it('says the prompt is unfiled when it belongs to no collection', async () => {
		const { el } = mountDetail(undefined);
		await tick();

		const section = collectionsSection(el);
		expect(section.textContent).toContain('Not in any collection.');
		expect(section.querySelector('[data-testid="prompt-collection-chips"]')).toBeNull();
	});
});
