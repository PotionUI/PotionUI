// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { tick } from 'svelte';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({
			get: vi.fn().mockResolvedValue({ data: { data: {} } }),
			put: vi.fn().mockResolvedValue({ data: { success: true } })
		})
	}
}));

// jsdom has no ResizeObserver. PromptSegment falls back to its widest,
// most-expanded shape without one — most of these tests rely on exactly
// that — but a few need to drive the header's own narrow-width behavior
// (the description-as-icon fallback), which only exists on the other side
// of that observer. This fake lets a test reach in and fire it directly.
class FakeResizeObserver {
	static instances: FakeResizeObserver[] = [];
	private callback: ResizeObserverCallback;
	private observedTargets: Element[] = [];

	constructor(callback: ResizeObserverCallback) {
		this.callback = callback;
		FakeResizeObserver.instances.push(this);
	}

	// PromptSegment observes the card first, then the name button — record
	// both so `trigger` can attribute a width to the right one, the same way
	// the component's own `entry.target === cardEl` branching does.
	observe(target: Element) {
		this.observedTargets.push(target);
	}

	unobserve() {}
	disconnect() {}

	trigger(width: number, targetIndex = 0) {
		const target = this.observedTargets[targetIndex];
		this.callback(
			[{ contentRect: { width }, target } as ResizeObserverEntry],
			this as unknown as ResizeObserver
		);
	}
}

const { default: PromptSegment } = await import('../../src/lib/components/PromptSegment.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { promptSegmentActionPins } = await import('../../src/lib/stores/promptSegmentActionPins');

function segment(partial: Record<string, unknown> = {}) {
	return {
		id: 'seg-1',
		content: 'cinematic portrait of a lighthouse keeper',
		type: 'content',
		chips: {},
		enabled: true,
		...partial
	};
}

function mount(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: PromptSegment as never,
		target,
		props: { segment: segment(), index: 0, total: 3, ...props }
	});

	const buttons = () => Array.from(target.querySelectorAll('button'));
	return {
		target,
		component,
		buttons,
		byLabel: (label: string) =>
			buttons().find((b) => b.getAttribute('aria-label') === label) as HTMLButtonElement | undefined,
		text: () => target.textContent || ''
	};
}

function openMenu(card: ReturnType<typeof mount>, startsWith: string) {
	const trigger = card.buttons().find((b) => (b.getAttribute('aria-label') || '').startsWith(startsWith));
	trigger?.click();
}

function menuItemLabels(): string[] {
	return Array.from(document.querySelectorAll('[role="menuitem"]')).map((el) => (el.textContent || '').trim());
}

beforeEach(() => {
	FakeResizeObserver.instances = [];
	(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = FakeResizeObserver;
	promptSegmentActionPins.reset();
});

afterEach(() => {
	document.body.innerHTML = '';
});

describe('the header action cluster', () => {
	it('shows no per-segment actions inline by default — everything lives in the overflow menu', () => {
		const card = mount();
		expect(card.byLabel('Disable')).toBeUndefined();
		expect(card.byLabel('Duplicate')).toBeUndefined();
		expect(card.byLabel('Details')).toBeUndefined();
		expect(card.byLabel('Save')).toBeUndefined();
	});

	it('always shows exactly drag, Remove segment and the overflow trigger when nothing is pinned', () => {
		const card = mount();
		expect(card.target.querySelectorAll('.icon-btn')).toHaveLength(3);
		expect(card.byLabel('Remove segment')).toBeTruthy();
	});

	it('calls the remove handler when Remove segment is clicked', () => {
		const card = mount();
		const onRemove = vi.fn();
		(card.component as unknown as { $on: (event: string, cb: () => void) => void }).$on(
			'remove',
			onRemove
		);

		card.byLabel('Remove segment')?.click();

		expect(onRemove).toHaveBeenCalledTimes(1);
	});

	it('disables Remove segment when it is the only segment left', () => {
		const card = mount({ total: 1 });
		expect(card.byLabel('Remove segment')?.disabled).toBe(true);
	});

	it('keeps Remove segment visible once the header collapses to its narrow width', () => {
		const card = mount();
		FakeResizeObserver.instances.at(-1)?.trigger(300);
		return Promise.resolve().then(() => {
			expect(card.byLabel('Remove segment')).toBeTruthy();
		});
	});

	it('reaches every action through the overflow menu, Disable/Enable flipped by state', async () => {
		const enabled = mount();
		openMenu(enabled, 'More actions for');
		await Promise.resolve();
		expect(menuItemLabels()).toEqual(
			expect.arrayContaining(['Duplicate', 'Disable', 'Details', 'Save'])
		);

		document.body.innerHTML = '';
		const disabled = mount({ segment: segment({ enabled: false }) });
		openMenu(disabled, 'More actions for');
		await Promise.resolve();
		expect(menuItemLabels()).toEqual(expect.arrayContaining(['Enable']));
		expect(menuItemLabels()).not.toContain('Disable');
	});

	it('renders the same actions in the menu whether the segment is named or not', async () => {
		const named = mount({ segment: segment({ name: 'Subject' }) });
		openMenu(named, 'More actions for');
		await Promise.resolve();
		const namedItems = menuItemLabels();
		document.body.innerHTML = '';

		const unnamed = mount();
		openMenu(unnamed, 'More actions for');
		await Promise.resolve();
		expect(menuItemLabels()).toEqual(namedItems);
	});

	it('has no separate footer element left in the card', () => {
		const card = mount();
		expect(card.target.querySelector('.segment-foot')).toBeNull();
	});

	it('reaches the drag handle and overflow menu alongside Remove segment', () => {
		const card = mount();
		expect(card.byLabel('Drag positive segment 1 of 3 to reorder')).toBeTruthy();
		expect(card.byLabel('More actions for positive segment 1 of 3')).toBeTruthy();
	});

	it('opens the details modal from the menu, not an inline reveal', async () => {
		const card = mount();
		expect(card.target.querySelector('.card-details')).toBeNull();

		openMenu(card, 'More actions for');
		await Promise.resolve();
		const details = Array.from(document.querySelectorAll('[role="menuitem"]')).find(
			(el) => (el.textContent || '').trim() === 'Details'
		) as HTMLButtonElement | undefined;
		details?.click();
		await Promise.resolve();

		expect(document.body.textContent).toContain('Segment details');
		expect(card.target.querySelector('.card-details')).toBeNull();
	});

	it('clicking the name also opens the details modal', async () => {
		const card = mount({ segment: segment({ name: 'Subject' }) });
		card.byLabel('Rename positive segment 1 of 3')?.click();
		await Promise.resolve();

		expect(document.body.textContent).toContain('Segment details');
	});
});

describe('pinning an action', () => {
	function pinToggle(label: string) {
		const item = Array.from(document.querySelectorAll('[role="menuitem"]')).find(
			(el) => (el.textContent || '').trim() === label
		);
		return item?.parentElement?.querySelector('.menu-pin') as HTMLButtonElement | undefined;
	}

	it('starts unpressed and, once pinned, adds an inline header button', async () => {
		const card = mount();
		openMenu(card, 'More actions for');
		await Promise.resolve();

		const pin = pinToggle('Duplicate');
		expect(pin).toBeTruthy();
		expect(pin?.getAttribute('aria-pressed')).toBe('false');
		expect(card.byLabel('Duplicate')).toBeUndefined();

		pin?.click();
		await tick();
		await tick();

		expect(card.byLabel('Duplicate')).toBeTruthy();
		expect(card.byLabel('Duplicate')?.classList.contains('reveal')).toBe(true);
		expect(pinToggle('Duplicate')?.getAttribute('aria-pressed')).toBe('true');
	});

	it('un-pins from the same menu toggle, dropping the inline button again', async () => {
		const card = mount();
		openMenu(card, 'More actions for');
		await Promise.resolve();
		pinToggle('Duplicate')?.click();
		await Promise.resolve();
		expect(card.byLabel('Duplicate')).toBeTruthy();

		pinToggle('Duplicate')?.click();
		await Promise.resolve();

		expect(card.byLabel('Duplicate')).toBeUndefined();
	});

	it('is a global preference shared by every mounted segment', async () => {
		const first = mount();
		openMenu(first, 'More actions for');
		await Promise.resolve();
		pinToggle('Duplicate')?.click();
		await Promise.resolve();

		const second = mount({ index: 1 });
		expect(second.byLabel('Duplicate')).toBeTruthy();
	});

	it('clicking the inline pinned button runs the action itself', async () => {
		const card = mount();
		openMenu(card, 'More actions for');
		await Promise.resolve();
		pinToggle('Duplicate')?.click();
		await Promise.resolve();

		const onDuplicate = vi.fn();
		(card.component as unknown as { $on: (event: string, cb: () => void) => void }).$on('duplicate', onDuplicate);
		card.byLabel('Duplicate')?.click();

		expect(onDuplicate).toHaveBeenCalledTimes(1);
	});
});

describe('the content card overflow menu', () => {
	it('offers every action, including the three inserts, on a content segment', async () => {
		const card = mount();
		openMenu(card, 'More actions for');
		await Promise.resolve();

		expect(menuItemLabels()).toEqual(
			expect.arrayContaining([
				'Move up',
				'Move down',
				'Details',
				'Save',
				'Replace from saved',
				'Duplicate',
				'Disable'
			])
		);
	});

	it('runs an insert action against this segment\'s own editor and closes the menu', async () => {
		const card = mount();
		openMenu(card, 'More actions for');
		await Promise.resolve();
		expect(document.querySelector('[role="menu"]')).toBeTruthy();

		const insertChoice = Array.from(document.querySelectorAll('[role="menuitem"]')).find((el) =>
			(el.textContent || '').includes('Insert a choice group')
		) as HTMLButtonElement | undefined;
		expect(insertChoice).toBeTruthy();
		insertChoice?.click();
		await Promise.resolve();

		expect(document.querySelector('[role="menu"]')).toBeNull();
	});
});

describe('the card head', () => {
	it('shows the segment name when it has one', () => {
		const card = mount({ segment: segment({ name: 'Subject' }) });
		expect(card.text()).toContain('Subject');
	});

	it('invites a name with a short, sentence-case placeholder', () => {
		const card = mount();
		expect(card.text()).toContain('Unnamed');
		expect(card.text()).not.toContain('Name this segment');
	});

	it('shows no chip on an enabled text segment', () => {
		expect(mount().target.querySelector('.state-chip')).toBeNull();
	});

	it('shows an Off chip once the segment is disabled', () => {
		const card = mount({ segment: segment({ name: 'Lighting', enabled: false }) });
		expect(card.target.querySelector('.off-chip')).toBeTruthy();
	});

	it('reports its own character count as a bare number in the header, with a tooltip', () => {
		const card = mount({ segment: segment({ content: 'harsh noon sun, hard shadows' }) });
		const count = card.target.querySelector('.char-count');
		expect(count?.textContent?.trim()).toBe('28');
		expect(card.text()).not.toContain('28 chars');
	});

	it('numbers the card from one, zero-padded', () => {
		expect(mount({ index: 0 }).text()).toContain('01');
		expect(mount({ index: 11 }).text()).toContain('12');
	});
});

describe('description', () => {
	it('renders inline in the header when present', () => {
		const card = mount({ segment: segment({ description: 'A quick mood note' }) });
		const description = card.target.querySelector('.head-description');
		expect(description?.textContent).toBe('A quick mood note');
	});

	it('renders nothing extra when the segment has no description', () => {
		const card = mount();
		expect(card.target.querySelector('.head-description')).toBeNull();
		expect(card.target.querySelector('.description-icon')).toBeNull();
	});

	it('falls back to an info icon once the header collapses to its narrow width', () => {
		const card = mount({ segment: segment({ description: 'A quick mood note' }) });
		expect(card.target.querySelector('.head-description')).toBeTruthy();

		FakeResizeObserver.instances.at(-1)?.trigger(300);
		return Promise.resolve().then(() => {
			expect(card.target.querySelector('.head-description')).toBeNull();
			const icon = card.target.querySelector('.description-icon');
			expect(icon).toBeTruthy();
			expect(icon?.getAttribute('aria-label')).toContain('A quick mood note');
		});
	});

	it('does not render the old template-slot line at all', () => {
		const card = mount({
			segment: segment({ template: { id: 't1', name: 'Portrait', slot: 'subject', position: 0 } })
		});
		expect(card.text()).not.toContain('from template slot');
	});
});

describe('colour', () => {
	it('sets a left rail colour on the card when the segment has one', () => {
		const card = mount({ segment: segment({ color: '#2ec9b0' }) });
		const cardEl = card.target.querySelector('.card') as HTMLElement;
		expect(cardEl.classList.contains('has-color')).toBe(true);
		expect(cardEl.style.getPropertyValue('--seg-color')).toBe('#2ec9b0');
	});

	it('carries no colour rail when the segment has none', () => {
		const card = mount();
		const cardEl = card.target.querySelector('.card') as HTMLElement;
		expect(cardEl.classList.contains('has-color')).toBe(false);
	});
});
