// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { tick } from 'svelte';
import type { PromptSyntaxSpec } from '$lib/utils/promptSyntax';
import type { PromptResourceSpec } from '$lib/utils/promptResources';

const { default: ResolvedPromptPreview } = await import(
	'../../src/lib/components/ResolvedPromptPreview.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

let mounted: { target: HTMLElement; destroy: () => void } | undefined;

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: ResolvedPromptPreview as never, target, props });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

function openBody(target: HTMLElement) {
	(target.querySelector('button') as HTMLButtonElement)?.click();
}

const dialogueSpec: PromptSyntaxSpec = {
	token: '<d></d>',
	kind: 'wrap',
	pattern: '<d>[\\s\\S]*?</d>',
	insert: '<d>{}</d>',
	help: "Wraps a speaker's exact words, preserved verbatim",
	tone: 'signal'
};

const pictureSpec: PromptSyntaxSpec = {
	token: '<Picture 1>',
	kind: 'marker',
	pattern: '<Picture \\d+>',
	insert: '<Picture {n=1}>',
	help: 'References a picture reference by its 1-based position',
	tone: 'info'
};

const resourceSpecs: PromptResourceSpec[] = [{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }];

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('ResolvedPromptPreview prompt-syntax highlighting', () => {
	it('renders a matched marker as one tinted span with a tooltip', async () => {
		mounted = mount({
			prompt: '(S1) says hello',
			promptSyntax: [
				{ token: '(S1)', kind: 'marker', pattern: '\\(S\\d+\\)', help: 'Speaker reference', tone: 'accent' }
			]
		});
		openBody(mounted.target);
		await tick();

		const span = Array.from(mounted.target.querySelectorAll('span')).find((s) => s.textContent === '(S1)');
		expect(span).toBeDefined();
		expect(span!.className).toMatch(/bg-|text-/);
	});

	it('highlights a multi-line <d></d> dialogue block as a single span, excluding the trailing text after it', async () => {
		mounted = mount({
			prompt: '(S1) says: <d>I cry\nI scream\n...\nUnderneath</d>.',
			promptSyntax: [dialogueSpec]
		});
		openBody(mounted.target);
		await tick();

		const spans = Array.from(mounted.target.querySelectorAll('span'));
		const dialogueSpan = spans.find((s) => s.textContent?.startsWith('<d>I cry'));
		expect(dialogueSpan).toBeDefined();
		expect(dialogueSpan!.textContent).toBe('<d>I cry\nI scream\n...\nUnderneath</d>');
		expect(dialogueSpan!.textContent).not.toContain('</d>.');

		expect(mounted.target.textContent).toContain('<d>I cry\nI scream\n...\nUnderneath</d>.');
	});

	it('highlights a resolved @-resource marker the same way as literal typed entity-reference text', async () => {
		mounted = mount({
			prompt: 'shows @[references:a.png] clearly',
			promptResources: resourceSpecs,
			resourceFieldValues: { references: [{ relative_path: 'a.png' }] },
			promptSyntax: [pictureSpec]
		});
		openBody(mounted.target);
		await tick();

		const span = Array.from(mounted.target.querySelectorAll('span')).find((s) => s.textContent === '<Picture 1>');
		expect(span).toBeDefined();
	});

	it('highlights literal typed entity-reference text identically to the resolved form', async () => {
		mounted = mount({
			prompt: 'shows <Picture 1> clearly',
			promptSyntax: [pictureSpec]
		});
		openBody(mounted.target);
		await tick();

		const span = Array.from(mounted.target.querySelectorAll('span')).find((s) => s.textContent === '<Picture 1>');
		expect(span).toBeDefined();
	});
});
