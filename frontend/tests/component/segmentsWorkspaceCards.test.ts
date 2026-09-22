import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { SavedSegment, SegmentCategory } from '$lib/types/segments';

const { default: SegmentCard } = await import('../../src/routes/prompts/sections/SegmentCard.svelte');
const { default: CategoryCard } = await import('../../src/routes/prompts/sections/CategoryCard.svelte');

function segment(overrides: Partial<SavedSegment> = {}): SavedSegment {
	return {
		id: 'seg-1',
		name: 'Golden hour',
		category_id: 'cat-light',
		type: 'content',
		content: 'warm golden hour sunlight, long soft shadows, gentle lens flare',
		chips: {},
		enabled: true,
		tags: ['portrait', 'outdoor', 'warm'],
		effective_color: '#F59E0B',
		updated_at: '2026-09-22T00:00:00.000Z',
		...overrides
	};
}

function category(overrides: Partial<SegmentCategory> = {}): SegmentCategory {
	return {
		id: 'cat-cam',
		name: 'Camera',
		description: 'Lens, angle and movement fragments for any shot.',
		color: '#3B82F6',
		...overrides
	};
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountSegmentCard(props: Partial<Record<string, unknown>> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(SegmentCard, {
		target,
		props: {
			segment: segment(),
			categoryName: 'Lighting',
			onToggleSelect: vi.fn(),
			onOpen: vi.fn(),
			onInsert: vi.fn(),
			onDuplicate: vi.fn(),
			onDelete: vi.fn(),
			...props
		}
	});
	flushSync();
	return target.querySelector('[data-segment-card]') as HTMLElement;
}

function mountCategoryCard(props: Partial<Record<string, unknown>> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(CategoryCard, {
		target,
		props: {
			category: category(),
			count: 12,
			onToggleSelect: vi.fn(),
			onOpen: vi.fn(),
			onNewSegment: vi.fn(),
			onDelete: vi.fn(),
			...props
		}
	});
	flushSync();
	return target.querySelector('[data-category-card]') as HTMLElement;
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('SegmentCard', () => {
	it('renders the name, type badge, category badge and at most two tags', () => {
		const card = mountSegmentCard();
		expect(card).toBeTruthy();
		expect(card.textContent).toContain('Golden hour');
		expect(card.textContent).toContain('content');
		expect(card.textContent).toContain('Lighting');
		expect(card.textContent).toContain('#portrait');
		expect(card.textContent).toContain('#outdoor');
		expect(card.textContent).not.toContain('#warm');
	});

	it('shows the break placeholder and a Disabled badge for a disabled break segment', () => {
		const card = mountSegmentCard({ segment: segment({ type: 'break', content: '', enabled: false }) });
		expect(card.textContent).toContain('Prompt break');
		expect(card.textContent).toContain('break');
		expect(card.textContent).toContain('Disabled');
	});

	it('opens on Enter and toggles selection on Space', () => {
		const onOpen = vi.fn();
		const onToggleSelect = vi.fn();
		const card = mountSegmentCard({ onOpen, onToggleSelect });
		card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		expect(onOpen).toHaveBeenCalledTimes(1);
		expect(onOpen.mock.calls[0][0].id).toBe('seg-1');
		card.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		expect(onToggleSelect).toHaveBeenCalledTimes(1);
		expect(onOpen).toHaveBeenCalledTimes(1);
	});

	it('renders the hover actions inside the footer row', () => {
		const card = mountSegmentCard({ segment: segment({ name: 'Golden hour with long soft shadows' }) });
		target.style.width = '300px';
		flushSync();
		const title = card.querySelector('h3') as HTMLElement;
		const footer = card.querySelector('[data-card-footer]') as HTMLElement;
		const actions = card.querySelector('[data-card-actions]') as HTMLElement;
		expect(title.textContent).toBe('Golden hour with long soft shadows');
		expect(footer.contains(actions)).toBe(true);
		expect(actions.classList.contains('absolute')).toBe(false);
		expect(title.parentElement!.contains(actions)).toBe(false);
	});

	it('calls onInsert from the Insert button without opening the card', () => {
		const onOpen = vi.fn();
		const onInsert = vi.fn();
		const card = mountSegmentCard({ onOpen, onInsert });
		const insert = Array.from(card.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Insert');
		expect(insert).toBeTruthy();
		insert!.click();
		expect(onInsert).toHaveBeenCalledTimes(1);
		expect(onOpen).not.toHaveBeenCalled();
	});
});

describe('CategoryCard', () => {
	it('renders the name, count and description', () => {
		const card = mountCategoryCard();
		expect(card).toBeTruthy();
		expect(card.textContent).toContain('Camera');
		expect(card.textContent).toContain('12 segments');
		expect(card.textContent).toContain('Lens, angle and movement');
	});

	it('renders the full name in a 300px container with the actions inside the footer row', () => {
		const card = mountCategoryCard({ category: category({ name: 'Cinematic lighting setups' }) });
		target.style.width = '300px';
		flushSync();
		const title = card.querySelector('h3') as HTMLElement;
		const footer = card.querySelector('[data-card-footer]') as HTMLElement;
		const actions = card.querySelector('[data-card-actions]') as HTMLElement;
		expect(title.textContent).toBe('Cinematic lighting setups');
		expect(footer.contains(actions)).toBe(true);
		expect(actions.classList.contains('absolute')).toBe(false);
		expect(title.parentElement!.contains(actions)).toBe(false);
		expect(card.textContent).toContain('New segment');
	});

	it('falls back to "No description" when the category has none', () => {
		const card = mountCategoryCard({ category: category({ description: '' }), count: 0 });
		expect(card.textContent).toContain('No description');
		expect(card.textContent).toContain('No segments');
	});

	it('opens on Enter and toggles selection on Space', () => {
		const onOpen = vi.fn();
		const onToggleSelect = vi.fn();
		const card = mountCategoryCard({ onOpen, onToggleSelect });
		card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		expect(onOpen).toHaveBeenCalledTimes(1);
		expect(onOpen.mock.calls[0][0].id).toBe('cat-cam');
		card.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
		expect(onToggleSelect).toHaveBeenCalledTimes(1);
	});
});
