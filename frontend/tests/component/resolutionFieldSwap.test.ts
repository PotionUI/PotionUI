// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';
import ResolutionField from '$lib/components/form-fields/ResolutionField.svelte';

const flush = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
let instance: ReturnType<typeof mount> | null = null;

async function render(value: string, options: string[]) {
	const onChange = vi.fn();
	instance = mount(ResolutionField as never, {
		target: document.body,
		props: { name: 'resolution', config: { title: 'Resolution', options }, value, onChange }
	});
	await flush(10);
	return onChange;
}

const swapButton = () => document.body.querySelector<HTMLButtonElement>('button[aria-label^="Swap"], button[aria-label^="No "], button[aria-label^="Square"]');

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

describe('ResolutionField swap button', () => {
	it('swaps to the mirrored option and fires onChange once', async () => {
		const onChange = await render('1920x1080', ['1920x1080', '1080x1920']);
		const btn = swapButton()!;
		expect(btn.getAttribute('aria-label')).toBe('Swap to 1080×1920');
		expect(btn.disabled).toBe(false);
		btn.click();
		await flush(10);
		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith('resolution', '1080x1920');
	});

	it('is disabled and silent for a square size', async () => {
		const onChange = await render('1024x1024', ['1024x1024', '1920x1080']);
		const btn = swapButton()!;
		expect(btn.disabled).toBe(true);
		btn.click();
		await flush(10);
		expect(onChange).not.toHaveBeenCalled();
	});

	it('is disabled with the reason when the mirror breaks the custom limits', async () => {
		const onChange = await render('1140x1472', ['1140x1472', '1280x720']);
		const btn = swapButton()!;
		expect(btn.disabled).toBe(true);
		expect(btn.getAttribute('aria-label')).toBe('No 1472×1140 option for this model');
		btn.click();
		expect(onChange).not.toHaveBeenCalled();
	});

	it('is not rendered when every option is square', async () => {
		await render('512x512', ['512x512', '768x768']);
		expect(swapButton()).toBeNull();
	});

	it('shows the tooltip text on hover', async () => {
		await render('1920x1080', ['1920x1080', '1080x1920']);
		const wrapper = swapButton()!.parentElement!;
		wrapper.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
		wrapper.dispatchEvent(new MouseEvent('mouseenter'));
		await flush(400);
		expect(document.body.textContent).toContain('Swap to 1080×1920');
	});
});
