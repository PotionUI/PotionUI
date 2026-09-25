// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { tick } from 'svelte';
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

const specs: PromptResourceSpec[] = [{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }];

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('ResolvedPromptPreview resource marker resolution', () => {
	it('shows the resolved token in the collapsed summary line', () => {
		mounted = mount({
			prompt: 'a cat @[references:a.png]',
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png' }] }
		});
		expect(mounted.target.textContent).toContain('a cat <Picture 1>');
		expect(mounted.target.textContent).not.toContain('@[references:a.png]');
	});

	it('shows the resolved token in the expanded body for both prompt and negative', async () => {
		mounted = mount({
			prompt: 'a cat @[references:a.png]',
			negativePrompt: 'no @[references:a.png] please',
			promptResources: specs,
			resourceFieldValues: { references: [{ relative_path: 'a.png' }] }
		});
		(mounted.target.querySelector('button') as HTMLButtonElement)?.click();
		await tick();
		expect(mounted.target.textContent).toContain('a cat <Picture 1>');

		const negativeTab = Array.from(mounted.target.querySelectorAll('button')).find(
			(b) => b.textContent?.trim() === 'Negative'
		);
		negativeTab?.click();
		await tick();
		expect(mounted.target.textContent).toContain('no <Picture 1> please');
	});

	it('leaves a dangling marker unresolved in the preview', () => {
		mounted = mount({
			prompt: 'a cat @[references:gone.png]',
			promptResources: specs,
			resourceFieldValues: { references: [] }
		});
		expect(mounted.target.textContent).toContain('@[references:gone.png]');
	});
});
