// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { writable } from 'svelte/store';
import type { PromptResourceSpec } from '$lib/utils/promptResources';
import { RESOURCE_NUMBERING_CONTEXT_KEY, type ResourceNumbering } from '$lib/utils/resourceNumbering';

const { default: ResolvedPromptPreview } = await import('../../src/lib/components/ResolvedPromptPreview.svelte');
const { createClassComponent } = await import('svelte/legacy');

let mounted: { target: HTMLElement; destroy: () => void } | undefined;

const specs: PromptResourceSpec[] = [{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }];

function mount(props: Record<string, unknown>, numbering: ResourceNumbering) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: ResolvedPromptPreview as never,
		target,
		context: new Map([[RESOURCE_NUMBERING_CONTEXT_KEY, writable<ResourceNumbering | null>(numbering)]]),
		props
	});
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('ResolvedPromptPreview resource numbering context', () => {
	it('resolves markers using the per-shot position instead of the raw array index', () => {
		mounted = mount(
			{
				prompt: 'a cat @[references:c.png]',
				promptResources: specs,
				resourceFieldValues: { references: [{ relative_path: 'a.png' }, { relative_path: 'b.png' }, { relative_path: 'c.png' }] }
			},
			{ positionFor: (field, itemKey) => (field === 'references' && itemKey === 'c.png' ? 1 : null) }
		);
		expect(mounted.target.textContent).toContain('a cat <Picture 1>');
		expect(mounted.target.textContent).not.toContain('<Picture 3>');
	});
});
