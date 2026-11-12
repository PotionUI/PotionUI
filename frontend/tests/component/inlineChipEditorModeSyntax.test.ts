// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeAll, vi } from 'vitest';
import { flushSync } from 'svelte';
import type { PromptSyntaxSpec } from '$lib/utils/promptSyntax';

class FakeHighlight extends Set<Range> {}
const registry = new Map<string, FakeHighlight>();

beforeAll(() => {
	(globalThis as unknown as { Highlight: unknown }).Highlight = FakeHighlight;
	(globalThis as unknown as { CSS: unknown }).CSS = { highlights: registry };
});

const { default: InlineChipEditor } = await import('$lib/components/InlineChipEditor.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | undefined;

const loopSyntax: PromptSyntaxSpec[] = [
	{ token: 'integrated_multimodal_description:', kind: 'label', help: 'Opens the document', color: 'cornflowerblue' },
	{ token: '[Shot 1]', kind: 'marker', pattern: '\\[Shot 1\\]', help: 'The one shot of a loop', color: 'goldenrod' }
];

interface EditorInstance {
	insertSyntaxTrigger: () => void;
}

function mountEditor(value: string, promptSyntax: PromptSyntaxSpec[]) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: InlineChipEditor as never,
		target,
		props: { value, chips: {}, variant: 'segment-composer', borderless: true, promptSyntax }
	});
	return component as unknown as EditorInstance;
}

function highlightedText(): string[] {
	return [...registry.values()].flatMap((highlight) => [...highlight].map((range) => range.toString()));
}

afterEach(() => {
	component?.$destroy();
	target?.remove();
	document.body.innerHTML = '';
	registry.clear();
	vi.restoreAllMocks();
});

describe('InlineChipEditor with a mode that carries prompt syntax', () => {
	it('colours a token of the mode syntax', async () => {
		mountEditor('integrated_multimodal_description: [Shot 1] reeds sway', loopSyntax);
		await new Promise((r) => setTimeout(r, 0));
		flushSync();

		const coloured = highlightedText();
		expect(coloured).toContain('[Shot 1]');
		expect(coloured).toContain('integrated_multimodal_description:');
	});

	it('offers the / menu with the mode syntax tokens', () => {
		const editor = mountEditor('', loopSyntax);
		editor.insertSyntaxTrigger();
		flushSync();

		const text = document.querySelector('.syntax-picker')?.textContent ?? '';
		expect(text).toContain('integrated_multimodal_description:');
		expect(text).toContain('[Shot 1]');
	});

	it('colours nothing and lists no syntax tokens when the mode has none', async () => {
		const editor = mountEditor('integrated_multimodal_description: [Shot 1] reeds sway', []);
		await new Promise((r) => setTimeout(r, 0));
		flushSync();
		editor.insertSyntaxTrigger();
		flushSync();

		expect(highlightedText()).toEqual([]);
		const text = document.querySelector('.syntax-picker')?.textContent ?? '';
		expect(text).not.toContain('[Shot 1]');
		expect(text).not.toContain('integrated_multimodal_description:');
	});
});
