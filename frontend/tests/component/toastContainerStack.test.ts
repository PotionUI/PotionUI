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

describe('ToastContainer actions', () => {
	it('renders a primary and a secondary action and runs each one', () => {
		const ran: string[] = [];
		toasts.show('success', 'The sheet is kept.', {
			title: 'Saved 5 images to Library',
			duration: 0,
			action: { label: 'Open in Library', onClick: () => ran.push('open') },
			actions: [{ label: 'Use as references', onClick: () => ran.push('refs') }]
		});
		const { target } = mount();
		const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('[data-toast-action]'));
		expect(buttons.map((b) => b.textContent?.trim())).toEqual(['Open in Library', 'Use as references']);
		expect(buttons[0].className).toContain('bg-accent');
		expect(buttons[1].className).not.toContain('bg-accent');
		expect(buttons.every((b) => b.className.includes('text-sm'))).toBe(true);
		buttons[1].click();
		buttons[0].click();
		expect(ran).toEqual(['refs', 'open']);
	});

	it('keeps a single action toast unchanged', () => {
		toasts.show('info', 'Copied', { title: 'Done', duration: 0, action: { label: 'Undo', onClick: () => undefined } });
		const { target } = mount();
		expect(target.querySelectorAll('[data-toast-action]')).toHaveLength(1);
	});
});
