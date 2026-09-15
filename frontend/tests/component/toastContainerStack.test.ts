// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { get } from 'svelte/store';

const { default: ToastContainer } = await import('$lib/components/ToastContainer.svelte');
const { toasts } = await import('$lib/stores/toast');
const { createClassComponent } = await import('svelte/legacy');

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: ToastContainer as never, target, props: {} });
	return { target, component };
}

afterEach(() => {
	for (const t of get(toasts)) toasts.remove(t.id);
});

describe('ToastContainer stack cap', () => {
	it('caps the visible stack at 3 cards with a +N more chip, and flags the layer assertive on a danger toast', () => {
		toasts.show('success', 'Krea-2 Turbo', { title: 'Generation complete', duration: 0 });
		toasts.show('success', 'Upload finished', { title: 'Upload complete', duration: 0 });
		toasts.show('error', 'CUDA out of memory', { title: 'Generation failed', duration: 0 });
		toasts.show('info', 'civitai-provider enabled', { title: 'Plugin enabled', duration: 0 });
		toasts.show('warning', 'Free VRAM below 2GB', { title: 'Low VRAM', duration: 0 });

		const { target } = mount();

		const layer = target.querySelector('[role="status"]') as HTMLElement;
		expect(layer).toBeTruthy();
		expect(layer.getAttribute('aria-live')).toBe('assertive');

		const closeButtons = target.querySelectorAll('button[aria-label="Dismiss"]');
		expect(closeButtons).toHaveLength(3);

		expect(target.textContent).toContain('+2 more');
	});
});
