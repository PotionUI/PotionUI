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

describe('adding segments', () => {
	// e2e (fe74) reported "Add segment" clicked twice still leaving 1 listitem
	// in the list. Reproduced here to rule the component in or out: two clicks
	// append two rows, both inside the same role="list" container the mock's
	// anatomy renders (not a sibling of it) — confirms the real failure was
	// downstream (a focus-restoration race after the Details modal closes,
	// stealing a keystroke into a global shortcut — see fe70/fe74's own
	// `setSegmentMeta` fix), not this component's add/commit path.
	it('appends a row per click, every row inside the same list container', async () => {
		const editor = mount();
		const addBtn = () => editor.buttons().find((b) => (b.textContent || '').includes('Add segment'));
		// Each click's own reactive update needs to settle before the DOM (and
		// the button reference it re-renders) reflects it — a real click, with
		// its own dispatch + paint cycle, always clears that; two `.click()`
		// calls back-to-back in the same tick do not, which is a test-harness
		// gap, not the double-click bug e2e chased (that traced to a focus race
		// after the Details modal, not to this add/commit path — see the
		// describe block's own comment).
		addBtn()?.click();
		await Promise.resolve();
		addBtn()?.click();
		await Promise.resolve();

		const list = editor.target.querySelector('div[role="list"][aria-label="Positive segments"]');
		expect(list).not.toBeNull();
		expect(list?.querySelectorAll('[role="listitem"]')).toHaveLength(3);
		expect(editor.target.querySelectorAll('[role="listitem"]')).toHaveLength(3);
	});
});

describe('embedded (Video Director\'s stage beat)', () => {
	// StageBeat.svelte already gives this editor its own card (`.editor` in
	// its own stylesheet) — without `embedded`, the mock's `.composer` shell
	// nests a second, redundant container around the same segments.
	it('drops the composer card and toolbar, keeping the list and add row', () => {
		const editor = mount({ embedded: true, label: 'Prompt' });

		expect(editor.target.querySelector('.composer')).toBeNull();
		expect(editor.target.querySelector('.composer-toolbar')).toBeNull();
		expect(editor.target.querySelector('div[role="list"][aria-label="Prompt"]')).toBeTruthy();
		expect(editor.buttons().some((b) => (b.textContent || '').includes('Add segment'))).toBe(true);
	});

	it('renders the ordinary composer card when not embedded', () => {
		const editor = mount({ embedded: false, label: 'Prompt' });
		expect(editor.target.querySelector('.composer')).toBeTruthy();
		expect(editor.target.querySelector('.composer-toolbar')).toBeTruthy();
	});
});

describe('the Styles toolbar button', () => {
	it('is absent when the call site has no styles to offer', () => {
		const editor = mount();
		expect(editor.byText('Styles')).toBeUndefined();
	});

	it('appears first in the main toolbar, before Library, when the call site sets onOpenStyles', () => {
		const editor = mount({ onOpenStyles: () => {} });
		const styles = editor.byText('Styles');
		const library = editor.byText('Library');
		expect(styles).toBeTruthy();
		expect(library).toBeTruthy();
		expect(styles!.compareDocumentPosition(library!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('shows the applied style name instead of the bare label', () => {
		const editor = mount({ onOpenStyles: () => {}, appliedStyleName: '80s OVA Sci-Fi' });
		expect(editor.byText('Style: 80s OVA Sci-Fi')).toBeTruthy();
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
