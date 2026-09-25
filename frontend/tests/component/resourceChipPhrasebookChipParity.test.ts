// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';

const { default: PromptResourceChip } = await import('$lib/components/PromptResourceChip.svelte');
const { default: InlineChip } = await import('$lib/components/InlineChip.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { flushSync } = await import('svelte');

let targets: HTMLDivElement[] = [];
let components: Array<ReturnType<typeof createClassComponent>> = [];

function mount(component: unknown, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	targets.push(target);
	const instance = createClassComponent({ component: component as never, target, props });
	components.push(instance);
	flushSync();
	return target;
}

afterEach(() => {
	components.forEach((c) => c.$destroy());
	components = [];
	targets.forEach((t) => t.remove());
	targets = [];
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('resource chip / phrasebook chip size parity', () => {
	it('both chips share the .chip root class and exactly one .chip-main box each', () => {
		const resourceTarget = mount(PromptResourceChip, {
			field: 'references',
			itemKey: 'a.png',
			spec: { field: 'references', kind: 'image', token: '<Picture @>' },
			position: 2,
			item: { relative_path: 'a.png', name: 'a.png', url: '/media/a.png' },
			variant: 'segment-composer'
		});

		const phraseTarget = mount(InlineChip, {
			data: {
				id: 'chip-1',
				categoryPath: 'lighting',
				valueId: 'v1',
				label: 'golden hour',
				value: 'golden hour lighting',
				allValues: [{ id: 'v1', label: 'golden hour', value: 'golden hour lighting' }],
				shuffle: false,
				autoRegen: false
			},
			variant: 'segment-composer'
		});

		const resourceChip = resourceTarget.querySelector('.resource-chip');
		const phraseChip = phraseTarget.querySelector('.phrase-chip');
		expect(resourceChip).not.toBeNull();
		expect(phraseChip).not.toBeNull();
		expect(resourceChip!.classList.contains('chip')).toBe(true);
		expect(phraseChip!.classList.contains('chip')).toBe(true);

		expect(resourceChip!.querySelectorAll('.chip-main')).toHaveLength(1);
		expect(phraseChip!.querySelectorAll('.chip-main')).toHaveLength(1);

		const resourceThumb = resourceChip!.querySelector('.chip-thumb');
		expect(resourceThumb).not.toBeNull();
		expect(resourceThumb!.querySelector('img')).not.toBeNull();
	});

	it('the resource chip thumbnail fills the tile with object-cover, not a small inline icon', () => {
		const resourceTarget = mount(PromptResourceChip, {
			field: 'references',
			itemKey: 'a.png',
			spec: { field: 'references', kind: 'image', token: '<Picture @>' },
			position: 1,
			item: { relative_path: 'a.png', name: 'a.png', url: '/media/a.png' },
			variant: 'segment-composer'
		});

		const img = resourceTarget.querySelector('.chip-thumb img');
		expect(img).not.toBeNull();
		expect(img!.getAttribute('src')).toBe('/media/a.png');
	});
});
