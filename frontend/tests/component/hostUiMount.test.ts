import { describe, it, expect, afterEach, vi } from 'vitest';
import { getRegistry, registerLazyComponent } from '$lib/plugin-api/componentRegistry';
import { registerHostUiComponents } from '$lib/plugin-api/hostUi';

const LOAD_TIMEOUT = { timeout: 20000 };

describe('host ui components mounted from plugin-side', () => {
	let target: HTMLElement;

	afterEach(() => target?.remove());

	function freshTarget(): HTMLElement {
		target = document.createElement('div');
		document.body.appendChild(target);
		return target;
	}

	it('mounts a Button with string children after the lazy load', async () => {
		registerHostUiComponents();
		const button = getRegistry().Button;
		const onclick = () => {};
		const handle = button.mount(freshTarget(), { variant: 'primary', onclick, children: 'Save' });
		await vi.waitFor(() => expect(target.querySelector('button')).not.toBeNull(), LOAD_TIMEOUT);
		const el = target.querySelector('button');
		expect(el?.textContent?.trim()).toBe('Save');
		expect(el?.className).toContain('bg-accent');
		button.unmount(handle);
	});

	it('applies prop updates made before and after the load', async () => {
		registerHostUiComponents();
		const badge = getRegistry().Badge;
		const handle = badge.mount(freshTarget(), { variant: 'neutral', children: 'One' });
		badge.update(handle, { variant: 'success' });
		await vi.waitFor(() => expect(target.innerHTML).toContain('text-success'), LOAD_TIMEOUT);
		expect(target.textContent).toContain('One');
		badge.update(handle, { variant: 'danger' });
		await vi.waitFor(() => expect(target.innerHTML).toContain('text-danger'));
		badge.unmount(handle);
	});

	it('renders function children into the host slot and cleans up', async () => {
		registerHostUiComponents();
		const button = getRegistry().Button;
		let cleaned = false;
		const handle = button.mount(freshTarget(), {
			children: (el: HTMLElement) => {
				const b = document.createElement('b');
				b.textContent = 'plugin node';
				el.append(b);
				return () => {
					cleaned = true;
				};
			}
		});
		await vi.waitFor(() => expect(target.querySelector('button b')).not.toBeNull(), LOAD_TIMEOUT);
		expect(target.querySelector('button b')?.textContent).toBe('plugin node');
		button.unmount(handle);
		expect(cleaned).toBe(true);
	});

	it('does not mount a component unmounted before its load finished', async () => {
		registerHostUiComponents();
		const kbd = getRegistry().Kbd;
		const handle = kbd.mount(freshTarget(), { keys: 'K' });
		kbd.unmount(handle);
		const unmounted = target;
		const probe = document.createElement('div');
		document.body.appendChild(probe);
		kbd.mount(probe, { keys: 'probe' });
		await vi.waitFor(() => expect(probe.textContent).toContain('probe'), LOAD_TIMEOUT);
		probe.remove();
		expect(unmounted.children.length).toBe(0);
	});

	it('renders a string slot prop as a named snippet', async () => {
		registerHostUiComponents();
		const alert = getRegistry().Alert;
		const handle = alert.mount(freshTarget(), { children: 'body text', actions: { slot: 'slot actions' } });
		await vi.waitFor(() => expect(target.textContent).toContain('slot actions'), LOAD_TIMEOUT);
		expect(target.textContent).toContain('body text');
		alert.unmount(handle);
	});

	it('renders a function slot prop as a named snippet', async () => {
		registerHostUiComponents();
		const alert = getRegistry().Alert;
		const handle = alert.mount(freshTarget(), {
			children: 'body',
			actions: { slot: (el: HTMLElement) => void (el.textContent = 'from slot fn') }
		});
		await vi.waitFor(() => expect(target.textContent).toContain('from slot fn'), LOAD_TIMEOUT);
		alert.unmount(handle);
	});

	it('mounts a Svelte 4 slot component with string children', async () => {
		registerHostUiComponents();
		const tooltip = getRegistry().Tooltip;
		const handle = tooltip.mount(freshTarget(), { text: 'tip', children: 'trigger text' });
		await vi.waitFor(() => expect(target.textContent).toContain('trigger text'), LOAD_TIMEOUT);
		tooltip.unmount(handle);
	});

	it('retries a failed lazy load on the next mount and reports the failure', async () => {
		const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
		let attempts = 0;
		registerLazyComponent('FlakyBadge', () => {
			attempts += 1;
			if (attempts === 1) return Promise.reject(new Error('chunk failed'));
			return import('$lib/components/ui/Badge.svelte');
		});
		const flaky = getRegistry().FlakyBadge;
		const first = flaky.mount(freshTarget(), { children: 'first' });
		await vi.waitFor(() => expect(errors).toHaveBeenCalled(), LOAD_TIMEOUT);
		expect(String(errors.mock.calls[0][0])).toContain('FlakyBadge');
		expect(target.textContent).toBe('');
		flaky.unmount(first);
		const second = flaky.mount(freshTarget(), { children: 'second' });
		await vi.waitFor(() => expect(target.textContent).toContain('second'), LOAD_TIMEOUT);
		expect(attempts).toBe(2);
		flaky.unmount(second);
		errors.mockRestore();
	});
});
