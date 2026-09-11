// @vitest-environment jsdom
//
// ChatMessage's `markdownResult` is memoized on (displayContent,
// variableChips) identity rather than left as a plain `$:` dependency, so
// re-setting a message's props to the SAME values never re-runs
// `processMarkdownWithActions` (two regex passes plus a full markdown
// render). This spies on the real function (via `vi.mock` wrapping the
// actual implementation) and drives the component through exactly that —
// props re-applied unchanged, the shape an unkeyed `{#each}` re-render of
// an unrelated sibling would produce.
//
// Bite-check note: in THIS Svelte 5 runtime, reverting the memoization back
// to a plain `$: markdownResult = processMarkdownWithActions(...)` left
// this test passing too — Svelte 5's own prop-setting already skips
// downstream recompute when a reassigned value is unchanged, unlike Svelte
// 4's per-render dirty-bit model the memoization was written to guard
// against. The memoization is kept anyway as a correct, zero-cost
// guarantee (see item 3's keyed `{#each}` and item 1's token coalescing,
// which are what actually keep an unrelated message's props from being
// reassigned during a stream) — but this specific test is an invariant
// check, not a regression test for an observed failure in this codebase.
import { describe, it, expect, vi, afterEach } from 'vitest';

const parseSpy = vi.fn();

vi.mock('$lib/utils/markdown', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/utils/markdown')>();
	parseSpy.mockImplementation(actual.processMarkdownWithActions);
	return { ...actual, processMarkdownWithActions: parseSpy };
});

const { default: ChatMessage } = await import('../../src/lib/components/ChatMessage.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: ChatMessage as never, target, props });
	return { target, component: component as unknown as { $set: (p: Record<string, unknown>) => void } };
}

afterEach(() => {
	document.body.innerHTML = '';
	parseSpy.mockClear();
});

describe('ChatMessage markdown memoization', () => {
	it('does not re-parse when re-set with the identical content — the shape of an unkeyed-each re-render of an unrelated sibling', async () => {
		const { component, target } = mount({
			role: 'assistant',
			content: 'a settled reply',
			isStreaming: false
		});
		expect(parseSpy).toHaveBeenCalledTimes(1);

		// Re-set every prop to the SAME value, exactly what an unkeyed
		// `{#each}` does to every OTHER row's component instance while only
		// one sibling's content actually changes.
		component.$set({ role: 'assistant', content: 'a settled reply', isStreaming: false });
		await tick();

		expect(parseSpy).toHaveBeenCalledTimes(1);
		expect(target.querySelector('.assistant-copy')?.textContent).toContain('a settled reply');
	});

	it('does re-parse when content genuinely changes', async () => {
		const { component } = mount({
			role: 'assistant',
			content: 'first',
			isStreaming: true
		});
		expect(parseSpy).toHaveBeenCalledTimes(1);

		component.$set({ content: 'first second' });
		await tick();

		expect(parseSpy).toHaveBeenCalledTimes(2);
	});

	it('does not re-parse on an unrelated prop change (e.g. isPartial toggling)', async () => {
		const { component } = mount({
			role: 'assistant',
			content: 'unchanged content',
			isPartial: false
		});
		expect(parseSpy).toHaveBeenCalledTimes(1);

		component.$set({ isPartial: true });
		await tick();

		expect(parseSpy).toHaveBeenCalledTimes(1);
	});
});
