// @vitest-environment jsdom
//
// Maintainer, live, Video Director stage (embedded mode): "the contenteditable
// below [the segment header] is ~20px wide — the prompt wraps letter by
// letter in a single narrow column down the page." `embedded` renders
// `.segment` with no `.composer.edge-inline` ancestor at all, and the
// edge-inline layout (single content column, rail absolutely positioned) was
// keyed off exactly that ancestor — without it, `.segment` fell back to its
// base two-column grid, squeezing `.segment-content` into the 42px rail
// track. jsdom can't measure real layout, so this asserts the structural
// contract instead — plus that the CSS fix itself (edge-inline made
// unconditional, not composer-scoped) is actually in the stylesheet.
import { readFileSync } from 'node:fs';
import { describe, it, expect, afterEach, vi } from 'vitest';

vi.mock('../../src/lib/utils/chipParser', () => ({
	hydrateSegments: async (segments: unknown[]) => segments
}));

const { default: SegmentedPromptEditor } = await import(
	'../../src/lib/components/SegmentedPromptEditor.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function segment() {
	return { id: 'seg-1', content: 'a lighthouse keeper', type: 'content', chips: {}, enabled: true };
}

let target: HTMLDivElement;

function mount(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({
		component: SegmentedPromptEditor as never,
		target,
		props: { segments: [segment()], ...props }
	});
	return target;
}

afterEach(() => {
	target?.remove();
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('embedded segment content width', () => {
	it('embedded mode renders the segment with no `.composer` ancestor at all', () => {
		const root = mount({ embedded: true, compact: true, label: 'Prompt' });
		const segmentEl = root.querySelector('.segment')!;
		expect(segmentEl).toBeTruthy();
		expect(segmentEl.closest('.composer')).toBeNull();
	});

	it('the content wrapper is a direct child of the segment, not nested inside the rail column, embedded or not', () => {
		for (const embedded of [true, false]) {
			target?.remove();
			const root = mount({ embedded, label: 'Prompt' });
			const segmentEl = root.querySelector('.segment')!;
			const rail = root.querySelector('.segment-rail')!;
			const content = root.querySelector('.segment-content')!;

			expect(content.parentElement).toBe(segmentEl);
			expect(rail.contains(content)).toBe(false);
		}
	});

	it('the edge-inline single-column layout is unconditional in the stylesheet, not scoped to `.composer.edge-inline`', () => {
		const css = readFileSync('src/lib/styles/segment-composer.css', 'utf8');
		// Bare (no `.composer`/`.composer.edge-inline` prefix) rules for both —
		// `embedded` mode never renders a `.composer` ancestor, so the single
		// content column and the head's 82px indent for the absolutely
		// positioned rail have to apply unconditionally to reach it.
		const bareSegmentGrid = /(?:^|\n)\.segment \{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/;
		const bareSegmentHeadIndent = /(?:^|\n)\.segment-head \{[^}]*padding-left:\s*82px/;
		expect(css).toMatch(bareSegmentGrid);
		expect(css).toMatch(bareSegmentHeadIndent);
	});
});
