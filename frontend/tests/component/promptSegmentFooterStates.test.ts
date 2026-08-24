// @vitest-environment jsdom
//
// The reworked card puts every control in a labelled footer strip that is
// always in the DOM — the old chrome only existed on hover, so "is it there"
// used to be unanswerable without a browser. These mount the real card and ask
// what a user can actually see and click in each state.
import { describe, it, expect, afterEach } from 'vitest';

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
		buttonText: () => buttons().map((b) => (b.textContent || '').trim()),
		byText: (text: string) =>
			buttons().find((b) => (b.textContent || '').trim() === text) as HTMLButtonElement | undefined,
		text: () => target.textContent || ''
	};
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('the card footer strip', () => {
	it('shows all four actions, labelled, without any hover', () => {
		const card = mount();
		expect(card.buttonText()).toEqual(expect.arrayContaining(['Disable', 'Duplicate', 'Details', 'Save']));
	});

	it('offers Enable instead of Disable once the segment is disabled', () => {
		const card = mount({ segment: segment({ enabled: false }) });

		expect(card.byText('Enable')).toBeTruthy();
		expect(card.byText('Disable')).toBeUndefined();
		expect(card.buttonText()).toEqual(expect.arrayContaining(['Duplicate', 'Details', 'Save']));
	});

	it('renders the same four actions whether the segment is named or not', () => {
		const named = mount({ segment: segment({ name: 'Subject' }) });
		const unnamed = mount();

		const footerOf = (card: ReturnType<typeof mount>) =>
			card.buttonText().filter((t) => ['Disable', 'Duplicate', 'Details', 'Save'].includes(t));

		expect(footerOf(named)).toEqual(footerOf(unnamed));
		expect(footerOf(named)).toHaveLength(4);
	});

	it('reports the resolved character count of its own content', () => {
		const card = mount({ segment: segment({ content: 'harsh noon sun, hard shadows' }) });
		expect(card.text()).toContain('28 chars');
	});
});

describe('the card head', () => {
	it('shows the segment name when it has one', () => {
		const card = mount({ segment: segment({ name: 'Subject' }) });
		expect(card.byText('Subject')).toBeTruthy();
	});

	it('invites a name when the segment has none', () => {
		expect(mount().byText('Name this segment')).toBeTruthy();
	});

	it('says a disabled segment is excluded from the resolved prompt', () => {
		const card = mount({ segment: segment({ name: 'Lighting', enabled: false }) });
		expect(card.text()).toContain('excluded from the resolved prompt');
	});

	it('numbers the card from one, zero-padded', () => {
		expect(mount({ index: 0 }).text()).toContain('01');
		expect(mount({ index: 11 }).text()).toContain('12');
	});
});

describe('the break row', () => {
	it('is a divider with its own handle and menu, not a card with a footer', () => {
		const row = mount({ segment: segment({ type: 'break', content: '' }) });

		expect(row.text()).toContain('BREAK');
		// None of the four footer actions belong to a break.
		expect(row.byText('Disable')).toBeUndefined();
		expect(row.byText('Duplicate')).toBeUndefined();
		expect(row.byText('Details')).toBeUndefined();
		expect(row.byText('Save')).toBeUndefined();
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
});

describe('the content card overflow menu', () => {
	it('does not repeat the actions the footer already shows', async () => {
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
});
