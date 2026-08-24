// @vitest-environment jsdom
//
// Two structural claims the rework makes that only a mount can check: the
// negative region is now the same anatomy as Prompt (header, cards, add row —
// no collapsed one-line summary), and the resolved panel appears only where the
// call site asks for it. `showPreview={false}` is load-bearing: the generate
// sidebar and Video Director both pass it, and a panel appearing there would be
// a visible change at a mount site nobody asked about.
import { describe, it, expect, afterEach, vi } from 'vitest';

vi.mock('../../src/lib/utils/chipParser', () => ({
	hydrateSegments: async (segments: unknown[]) => segments
}));

const { default: SegmentedPromptEditor } = await import(
	'../../src/lib/components/SegmentedPromptEditor.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function segment(id: string, content: string, partial: Record<string, unknown> = {}) {
	return { id, content, type: 'content', chips: {}, enabled: true, ...partial };
}

function mount(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: SegmentedPromptEditor as never,
		target,
		props: { segments: [segment('a', 'a lighthouse keeper')], ...props }
	});

	const buttons = () => Array.from(target.querySelectorAll('button'));
	return {
		target,
		component,
		buttons,
		byText: (text: string) =>
			buttons().find((b) => (b.textContent || '').trim() === text) as HTMLButtonElement | undefined,
		text: () => target.textContent || '',
		lists: () =>
			Array.from(target.querySelectorAll('[role="list"]')).map(
				(el) => el.getAttribute('aria-label') || ''
			)
	};
}

afterEach(() => {
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('the negative region', () => {
	it('repeats the Prompt anatomy — its own header, list and add row', () => {
		const editor = mount({ negativeSegments: [segment('n1', 'blurry')] });

		expect(editor.text()).toContain('Negative');
		expect(editor.lists()).toEqual(expect.arrayContaining(['Positive segments', 'Negative segments']));
		// One add row per region, both reachable without expanding anything.
		expect(editor.buttons().filter((b) => (b.textContent || '').includes('Add segment'))).toHaveLength(2);
	});

	it('counts its own segments, singular and plural', () => {
		expect(mount({ negativeSegments: [segment('n1', 'blurry')] }).text()).toContain('1 segment');
		expect(
			mount({ negativeSegments: [segment('n1', 'blurry'), segment('n2', 'watermark')] }).text()
		).toContain('2 segments');
	});

	it('warns that it is inert only when guidance actually makes it inert', () => {
		const inert = mount({ negativeSegments: [segment('n1', 'blurry')], negativeInert: true });
		expect(inert.text()).toContain('Not applied at current guidance');

		const live = mount({ negativeSegments: [segment('n1', 'blurry')] });
		expect(live.text()).not.toContain('Not applied at current guidance');
	});

	it('is absent entirely when the call site pairs no negative list', () => {
		const editor = mount();
		expect(editor.lists()).not.toContain('Negative segments');
	});
});

describe('the resolved panel', () => {
	it('is rendered when the call site asks for a preview', () => {
		const editor = mount({ showPreview: true });
		expect(editor.text()).toContain('What the model receives');
	});

	it('stays out of the way when the call site opts out', () => {
		const editor = mount({ showPreview: false });
		expect(editor.text()).not.toContain('What the model receives');
	});

	it('counts what the model receives, leaving disabled segments out', () => {
		const editor = mount({
			showPreview: true,
			segments: [segment('a', 'a forest'), segment('b', 'harsh noon sun', { enabled: false })]
		});

		expect(editor.text()).toContain(`${'a forest'.length} chars`);
		// The disabled card still shows its own text — the panel is what must not.
		expect(editor.text()).toContain('harsh noon sun');
		const panel = editor.target.querySelector('.resolved-body');
		expect(panel?.textContent).toBe('a forest');
	});

	it('counts breaks and renders BREAK as its own element, not as body text', () => {
		const editor = mount({
			showPreview: true,
			segments: [segment('a', 'x'), segment('b', '', { type: 'break' }), segment('c', 'y')]
		});

		expect(editor.text()).toContain('1 break');
		const pill = editor.target.querySelector('.resolved-break');
		expect(pill?.textContent).toBe('BREAK');
	});
});
