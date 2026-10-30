// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { tick } from 'svelte';

vi.mock('../../src/lib/utils/chipParser', () => ({
	hydrateSegments: async (segments: unknown[]) => segments
}));

const { default: SegmentedPromptEditor } = await import(
	'../../src/lib/components/SegmentedPromptEditor.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { api } = await import('../../src/lib/services/api/index');
vi.spyOn(api, 'getClient').mockReturnValue({
	get: vi.fn().mockResolvedValue({ data: { data: {} } }),
	put: vi.fn().mockResolvedValue({ data: { success: true } })
} as never);

afterEach(() => {
	document.body.innerHTML = '';
});

async function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({
		component: SegmentedPromptEditor as never,
		target,
		props: {
			segments: [{ id: 'a', content: 'a lighthouse keeper', type: 'content', chips: {}, enabled: true }],
			onOpenStyles: () => {},
			onOpenVariableManager: () => {},
			variables: { subject: { type: 'choice', options: ['cat'] } },
			...props
		}
	});
	for (let i = 0; i < 4; i++) await tick();
	return target;
}

const names = (target: HTMLElement) =>
	Array.from(
		target.querySelectorAll('header.composer-toolbar:not(.negative-header) [data-toolbar-action] button')
	).map((el) => el.getAttribute('aria-label'));

describe('prompt toolbar accessible names', () => {
	it('keeps the full name with the count when the text label is shown', async () => {
		const target = await mount({ compact: false });
		expect(names(target)).toContain('Variables (1)');
		expect(names(target)).toContain('Styles');
		expect(names(target)).toContain('Library');
	});

	it('keeps the full name with the count in compact mode', async () => {
		const target = await mount({ compact: true });
		expect(names(target)).toContain('Variables (1)');
		expect(names(target)).toContain('Styles');
		expect(names(target)).toContain('Library');
	});

	it('names the style that is applied', async () => {
		const target = await mount({ appliedStyleName: 'Cinematic' });
		expect(names(target)).toContain('Style: Cinematic');
	});
});
