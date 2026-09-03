// @vitest-environment jsdom
//
// The v3 card drops the separate footer strip: every action now lives in a
// single-line header, as an icon-only button with its old label carried over
// as its aria-label (and tooltip). These mount the real card and ask what a
// user can actually see and click in each state, the same way the old footer
// tests did.
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';

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

beforeEach(() => {
	FakeResizeObserver.instances = [];
	(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = FakeResizeObserver;
});

afterEach(() => {
	document.body.innerHTML = '';
});

describe('the header action cluster', () => {
	it('exposes all four content actions as icon-only buttons, aria-labelled, without any hover', () => {
		const card = mount();
		expect(card.byLabel('Disable')).toBeTruthy();
		expect(card.byLabel('Duplicate')).toBeTruthy();
		expect(card.byLabel('Details')).toBeTruthy();
		expect(card.byLabel('Save')).toBeTruthy();
	});

	it('always shows a seventh icon for Remove segment, alongside drag and more', () => {
		const card = mount();
		expect(card.target.querySelectorAll('.icon-btn')).toHaveLength(7);
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

	it('offers Enable instead of Disable once the segment is disabled', () => {
		const card = mount({ segment: segment({ enabled: false }) });

		expect(card.byLabel('Enable')).toBeTruthy();
		expect(card.byLabel('Disable')).toBeUndefined();
		expect(card.byLabel('Duplicate')).toBeTruthy();
		expect(card.byLabel('Details')).toBeTruthy();
		expect(card.byLabel('Save')).toBeTruthy();
	});

	it('renders the same four actions whether the segment is named or not', () => {
		const named = mount({ segment: segment({ name: 'Subject' }) });
		const unnamed = mount();

		const labelsOf = (card: ReturnType<typeof mount>) =>
			['Disable', 'Duplicate', 'Details', 'Save'].filter((label) => card.byLabel(label));

		expect(labelsOf(named)).toEqual(labelsOf(unnamed));
		expect(labelsOf(named)).toHaveLength(4);
	});

	it('has no separate footer element left in the card', () => {
		const card = mount();
		expect(card.target.querySelector('.card-footer')).toBeNull();
	});

	it('reaches the drag handle and overflow menu alongside the four actions', () => {
		const card = mount();
		expect(card.byLabel('Drag positive segment 1 of 3 to reorder')).toBeTruthy();
		expect(card.byLabel('More actions for positive segment 1 of 3')).toBeTruthy();
	});

	it('clicking Details opens the details modal, not an inline reveal', async () => {
		const card = mount();
		expect(card.target.querySelector('.card-details')).toBeNull();

		card.byLabel('Details')?.click();
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

	it('reports its own character count as a bare number, with a tooltip', () => {
		const card = mount({ segment: segment({ content: 'harsh noon sun, hard shadows' }) });
		const count = card.target.querySelector('.char-count');
		expect(count?.textContent?.trim()).toBe('28');
		expect(card.text()).not.toContain('28 chars');
		expect(card.text()).not.toContain('28 ch');
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

describe('the break row', () => {
	it('is a divider with its own handle and menu, not a card with header actions', () => {
		const row = mount({ segment: segment({ type: 'break', content: '' }) });

		expect(row.text()).toContain('BREAK');
		// None of the four content actions belong to a break.
		expect(row.byLabel('Disable')).toBeUndefined();
		expect(row.byLabel('Duplicate')).toBeUndefined();
		expect(row.byLabel('Details')).toBeUndefined();
		expect(row.byLabel('Save')).toBeUndefined();
	});

	it('also gets an always-visible Remove segment icon, wired to the same remove event', () => {
		const row = mount({ segment: segment({ type: 'break', content: '' }) });
		const onRemove = vi.fn();
		(row.component as unknown as { $on: (event: string, cb: () => void) => void }).$on(
			'remove',
			onRemove
		);

		expect(row.byLabel('Remove segment')).toBeTruthy();
		row.byLabel('Remove segment')?.click();

		expect(onRemove).toHaveBeenCalledTimes(1);
	});

	it('still reaches every action through its overflow menu', async () => {
		const row = mount({ segment: segment({ type: 'break', content: '' }) });
		const trigger = row
			.buttons()
			.find((b) => (b.getAttribute('aria-label') || '').startsWith('Actions for'));

		trigger?.click();
		await Promise.resolve();

		const items = Array.from(document.querySelectorAll('[role="menuitem"]')).map((el) =>
			(el.textContent || '').trim()
		);
		expect(items).toEqual(expect.arrayContaining(['Duplicate', 'Disable', 'Edit details']));
	});

	it('opens the same details modal from its overflow menu', async () => {
		const row = mount({ segment: segment({ type: 'break', content: '' }) });
		const trigger = row
			.buttons()
			.find((b) => (b.getAttribute('aria-label') || '').startsWith('Actions for'));
		trigger?.click();
		await Promise.resolve();

		const editDetails = Array.from(document.querySelectorAll('[role="menuitem"]')).find((el) =>
			(el.textContent || '').includes('Edit details')
		) as HTMLButtonElement | undefined;
		editDetails?.click();
		await Promise.resolve();

		expect(document.body.textContent).toContain('Segment details');
	});
});

describe('the content card overflow menu', () => {
	it('does not repeat the actions the header cluster already shows', async () => {
		const card = mount();
		const trigger = card
			.buttons()
			.find((b) => (b.getAttribute('aria-label') || '').startsWith('More actions for'));

		trigger?.click();
		await Promise.resolve();

		const items = Array.from(document.querySelectorAll('[role="menuitem"]')).map((el) =>
			(el.textContent || '').trim()
		);

		expect(items).toEqual(expect.arrayContaining(['Move up', 'Move down', 'Delete']));
		expect(items).not.toContain('Duplicate');
		expect(items).not.toContain('Disable');
		expect(items).not.toContain('Edit details');
	});

	it('opens the details modal from Edit details once the header has collapsed it into the menu', async () => {
		const card = mount();
		FakeResizeObserver.instances.at(-1)?.trigger(300);
		await Promise.resolve();

		const trigger = card
			.buttons()
			.find((b) => (b.getAttribute('aria-label') || '').startsWith('More actions for'));
		trigger?.click();
		await Promise.resolve();

		const editDetails = Array.from(document.querySelectorAll('[role="menuitem"]')).find((el) =>
			(el.textContent || '').includes('Edit details')
		) as HTMLButtonElement | undefined;
		expect(editDetails).toBeTruthy();
		editDetails?.click();
		await Promise.resolve();

		expect(document.body.textContent).toContain('Segment details');
	});
});
