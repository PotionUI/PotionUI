import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { Prompt } from '$lib/types/segments';
import { DEFAULT_PROMPT_FILTERS } from '$lib/prompts/promptFilters';

const { default: PromptsGrid } = await import('../../src/routes/prompts/components/PromptsGrid.svelte');

function prompt(overrides: Partial<Prompt> = {}): Prompt {
	return {
		id: 'prompt-1',
		name: 'Dancing girl factory',
		display_name: 'Dancing girl factory',
		segments: [],
		flattened_text: 'She eases into ${dance} keeping eye contact with the lens.',
		usage_hint: 'positive',
		model_name: 'MiniMax-H3',
		usage_count: 12,
		last_used_at: '2026-09-22T00:00:00.000Z',
		variables: {
			dance: { type: 'choice', options: ['breaking', 'waltz'], mode: 'shuffle', pinnedIndex: null }
		},
		...overrides
	};
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountGrid(overrides: Partial<Record<string, unknown>> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(PromptsGrid, {
		target,
		props: {
			prompts: [
				prompt(),
				prompt({
					id: 'prompt-2',
					name: 'Video negative, motion',
					flattened_text: 'blurry, low quality, watermark, jittery motion',
					variables: null
				}),
				prompt({
					id: 'prompt-3',
					name: 'Afrobeats waist wind',
					flattened_text: 'a dancer on a rooftop at dusk, smooth waist winds',
					variables: null
				})
			],
			loading: false,
			filters: DEFAULT_PROMPT_FILTERS,
			selectedIds: new Set<string>(),
			collections: [],
			onFiltersChange: vi.fn(),
			onLoadMore: vi.fn(),
			onOpen: vi.fn(),
			onUse: vi.fn(),
			onCopy: vi.fn(),
			onDuplicate: vi.fn(),
			onAddToCollection: vi.fn(),
			onExport: vi.fn(),
			onDeleteOne: vi.fn(),
			onToggleSelect: vi.fn(),
			onSelectAll: vi.fn(),
			onClearSelection: vi.fn(),
			onBulkAddToCollection: vi.fn(async () => true),
			onBulkCreateAndAddToCollection: vi.fn(async () => true),
			onBulkAddTag: vi.fn(),
			onBulkExport: vi.fn(),
			onBulkDelete: vi.fn(),
			onNewPrompt: vi.fn(),
			...overrides
		}
	});
	flushSync();
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('PromptsGrid cards', () => {
	it('renders the prompt name', () => {
		mountGrid();
		expect(target.textContent).toContain('Dancing girl factory');
	});

	it('clamps the flattened text and renders a ${var} usage as its own signal-mono span', () => {
		mountGrid();
		const card = target.querySelector('[data-prompt-card]') as HTMLElement;
		expect(card).toBeTruthy();
		const clamped = card.querySelector('.line-clamp-4') as HTMLElement;
		expect(clamped.textContent).toContain('She eases into');
		expect(clamped.textContent).toContain('${dance}');
		const varSpan = Array.from(clamped.querySelectorAll('span')).find((el) => el.textContent === '${dance}');
		expect(varSpan).toBeTruthy();
		expect(varSpan!.className).toContain('text-signal');
	});

	it('shows the variables badge with the count for a prompt that has variables', () => {
		mountGrid();
		const card = target.querySelector('[data-prompt-card]') as HTMLElement;
		const badges = Array.from(card.querySelectorAll('span')).map((el) => el.textContent?.trim());
		expect(badges).toContain('1');
	});

	it('does not show a variables badge for a prompt with none', () => {
		mountGrid();
		const cards = Array.from(target.querySelectorAll('[data-prompt-card]'));
		const secondCard = cards[1] as HTMLElement;
		expect(secondCard.textContent).toContain('Video negative, motion');
		expect(secondCard.querySelector('.text-signal')).toBeNull();
	});

	it('calls onOpen with the prompt when its Edit button is clicked - the control the workspace routes to ?id= from', () => {
		const onOpen = vi.fn();
		mountGrid({ onOpen });
		const card = target.querySelector('[data-prompt-card]') as HTMLElement;
		const editButton = Array.from(card.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Edit');
		expect(editButton).toBeTruthy();
		editButton!.click();
		expect(onOpen).toHaveBeenCalledTimes(1);
		expect(onOpen.mock.calls[0][0].id).toBe('prompt-1');
	});

	it('renders no toolbar of its own: search, Filters and sort live in the library shell header', () => {
		mountGrid();
		expect(target.querySelector('input[type="search"]')).toBeNull();
		expect(target.querySelector('select')).toBeNull();
		expect(Array.from(target.querySelectorAll('button')).some((b) => b.textContent?.trim().startsWith('Filters'))).toBe(false);
	});

	it('renders the cover thumbnail img with the media URL as-is, with no second size= param', () => {
		mountGrid({
			prompts: [prompt({ cover_thumbnail: '/api/media/files/f1?size=medium' })]
		});
		const card = target.querySelector('[data-prompt-card]') as HTMLElement;
		const img = card.querySelector('img') as HTMLImageElement;
		expect(img).toBeTruthy();
		expect(img.getAttribute('src')).toBe('/api/media/files/f1?size=medium');
	});

	it('shows an empty state when there are no prompts', () => {
		mountGrid({ prompts: [], total: 0 });
		expect(target.textContent).toContain('No prompts yet');
	});

	it('shows the filtered empty state with a Clear filters action that resets the filters', () => {
		const onFiltersChange = vi.fn();
		mountGrid({ prompts: [], filters: { ...DEFAULT_PROMPT_FILTERS, tags: ['dance'] }, onFiltersChange });
		expect(target.textContent).toContain('No prompts match these filters');
		const clear = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Clear filters');
		expect(clear).toBeTruthy();
		clear!.click();
		expect(onFiltersChange).toHaveBeenCalledTimes(1);
		expect(onFiltersChange.mock.calls[0][0].tags).toEqual([]);
	});
});
