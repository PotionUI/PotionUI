// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';
import ModelPickTrigger from '$lib/components/ui/ModelPickTrigger.svelte';

let instance: ReturnType<typeof mount> | null = null;

function render(props: Record<string, unknown>) {
	const onopen = vi.fn();
	const onclear = vi.fn();
	instance = mount(ModelPickTrigger, {
		target: document.body,
		props: { onopen, onclear, ...props }
	});
	return { onopen, onclear };
}

function clearButton() {
	return document.querySelector<HTMLButtonElement>('[aria-label="Clear model"]');
}

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

describe('ModelPickTrigger', () => {
	it('shows the placeholder and no clear when nothing is picked', () => {
		render({ value: null, placeholder: 'All models' });
		expect(document.body.textContent).toContain('All models');
		expect(clearButton()).toBeNull();
	});

	it('shows the picked name and a clear button', () => {
		render({ value: 'dreamshaper.safetensors' });
		expect(document.body.textContent).toContain('dreamshaper.safetensors');
		expect(clearButton()).not.toBeNull();
	});

	it('hides the clear button when clearing is not allowed', () => {
		render({ value: 'dreamshaper.safetensors', allowClear: false });
		expect(clearButton()).toBeNull();
	});

	it('clears without opening the picker', () => {
		const { onopen, onclear } = render({ value: 'dreamshaper.safetensors' });
		clearButton()!.click();
		expect(onclear).toHaveBeenCalledTimes(1);
		expect(onopen).not.toHaveBeenCalled();
	});

	it('does not let the clear click reach an ancestor', () => {
		const ancestor = vi.fn();
		document.body.addEventListener('click', ancestor);
		render({ value: 'dreamshaper.safetensors' });
		const event = new MouseEvent('click', { bubbles: true });
		const stop = vi.spyOn(event, 'stopPropagation');
		clearButton()!.dispatchEvent(event);
		expect(stop).toHaveBeenCalled();
		document.body.removeEventListener('click', ancestor);
	});

	it('opens the picker from the main button', () => {
		const { onopen, onclear } = render({ value: 'dreamshaper.safetensors' });
		document.querySelector<HTMLButtonElement>('button:not([aria-label])')!.click();
		expect(onopen).toHaveBeenCalledTimes(1);
		expect(onclear).not.toHaveBeenCalled();
	});

	it('is reachable and operable by keyboard as a native button', () => {
		render({ value: 'dreamshaper.safetensors' });
		const button = clearButton()!;
		expect(button.tagName).toBe('BUTTON');
		expect(button.tabIndex).toBe(0);
		expect(button.getAttribute('type')).toBe('button');
		button.focus();
		expect(document.activeElement).toBe(button);
	});
});
