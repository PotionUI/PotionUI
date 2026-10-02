// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const modals = {
	SavePromptModal: (await import('../../src/lib/components/modals/SavePromptModal.svelte')).default,
	UploadGenerationModal: (await import('../../src/lib/components/modals/UploadGenerationModal.svelte')).default,
	SegmentListApplyModal: (await import('../../src/lib/components/modals/SegmentListApplyModal.svelte')).default
};

let instance: ReturnType<typeof mount> | null = null;

function press(key: string) {
	const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
	document.body.dispatchEvent(event);
	flushSync();
	return event;
}

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

describe.each(Object.entries(modals))('%s keyboard', (_name, Component) => {
	function mountModal(isOpen: boolean) {
		const close = vi.fn();
		const cancel = vi.fn();
		const target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(Component as never, {
			target,
			props: { isOpen },
			events: { close, cancel }
		} as never);
		flushSync();
		return { close, cancel };
	}

	it('ignores Escape while closed', () => {
		const { close, cancel } = mountModal(false);
		const event = press('Escape');
		expect(event.defaultPrevented).toBe(false);
		expect(close).not.toHaveBeenCalled();
		expect(cancel).not.toHaveBeenCalled();
	});

	it('ignores Enter while closed', () => {
		const { close, cancel } = mountModal(false);
		const event = press('Enter');
		expect(event.defaultPrevented).toBe(false);
		expect(close).not.toHaveBeenCalled();
		expect(cancel).not.toHaveBeenCalled();
	});

	it('handles Escape while open', () => {
		const { close, cancel } = mountModal(true);
		press('Escape');
		expect(close.mock.calls.length + cancel.mock.calls.length).toBe(1);
	});
});
