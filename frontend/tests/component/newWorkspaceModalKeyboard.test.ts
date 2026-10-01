// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const { default: NewWorkspaceModal } = await import(
	'../../src/lib/components/modals/NewWorkspaceModal.svelte'
);

let instance: ReturnType<typeof mount> | null = null;

function mountModal(isOpen: boolean) {
	const save = vi.fn();
	const cancel = vi.fn();
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(NewWorkspaceModal, {
		target,
		props: { isOpen },
		events: { save, cancel, discard: vi.fn() }
	});
	flushSync();
	return { save, cancel };
}

function press(key: string) {
	document.body.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
	flushSync();
}

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

describe('NewWorkspaceModal keyboard', () => {
	it('ignores Enter and Escape while it is closed', () => {
		const { save, cancel } = mountModal(false);
		press('Enter');
		press('Escape');
		expect(save).not.toHaveBeenCalled();
		expect(cancel).not.toHaveBeenCalled();
	});

	it('saves on Enter while it is open', () => {
		const { save } = mountModal(true);
		press('Enter');
		expect(save).toHaveBeenCalledTimes(1);
	});

	it('cancels on Escape while it is open', () => {
		const { cancel } = mountModal(true);
		press('Escape');
		expect(cancel).toHaveBeenCalledTimes(1);
	});
});
