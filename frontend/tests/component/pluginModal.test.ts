// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

const { default: PluginModal } = await import('$lib/components/modals/PluginModal.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLDivElement | undefined;
let component: ReturnType<typeof createClassComponent> | undefined;

function mountModal(props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: PluginModal as never,
		target,
		props: { title: 'Plugin dialog', ...props }
	});
	return component;
}

function dialog() {
	return document.querySelector<HTMLElement>('[role="dialog"]');
}

function pressKey(key: string, eventTarget: EventTarget = window) {
	eventTarget.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
}

function wait(ms: number) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	target = undefined;
	document.body.innerHTML = '';
});

describe('PluginModal', () => {
	it('mounts the body container into the dialog and shows an appended node', () => {
		mountModal({
			mountBody: (el: HTMLElement) => {
				const marker = document.createElement('div');
				marker.textContent = 'plugin content';
				marker.setAttribute('data-testid', 'plugin-marker');
				el.appendChild(marker);
			},
			onConfirm: vi.fn(),
			onCancel: vi.fn()
		});

		const marker = dialog()?.querySelector('[data-testid="plugin-marker"]');
		expect(marker).toBeTruthy();
		expect(marker?.textContent).toBe('plugin content');
	});

	it('Enter is blocked while confirmDisabled, then confirms once enabled', async () => {
		const onConfirm = vi.fn();
		mountModal({
			confirmDisabled: true,
			mountBody: () => {},
			onConfirm,
			onCancel: vi.fn()
		});

		pressKey('Enter');
		expect(onConfirm).not.toHaveBeenCalled();

		component!.$set({ confirmDisabled: false });
		pressKey('Enter');
		expect(onConfirm).toHaveBeenCalledTimes(1);
	});

	it('Enter inside a textarea does not confirm', () => {
		const onConfirm = vi.fn();
		mountModal({
			mountBody: (el: HTMLElement) => {
				const textarea = document.createElement('textarea');
				textarea.setAttribute('data-testid', 'plugin-textarea');
				el.appendChild(textarea);
			},
			onConfirm,
			onCancel: vi.fn()
		});

		const textarea = dialog()!.querySelector('[data-testid="plugin-textarea"]') as HTMLTextAreaElement;
		pressKey('Enter', textarea);
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('Esc calls onCancel', () => {
		const onCancel = vi.fn();
		mountModal({
			mountBody: () => {},
			onConfirm: vi.fn(),
			onCancel
		});

		pressKey('Escape');
		expect(onCancel).toHaveBeenCalledTimes(1);
	});

	it('hideCancel omits the Cancel button', () => {
		mountModal({
			hideCancel: true,
			mountBody: () => {},
			onConfirm: vi.fn(),
			onCancel: vi.fn()
		});

		const cancelButton = Array.from(dialog()!.querySelectorAll('button')).find((b) =>
			(b.textContent || '').includes('Cancel')
		);
		expect(cancelButton).toBeUndefined();
	});

	it('shows the Cancel button by default', () => {
		mountModal({
			mountBody: () => {},
			onConfirm: vi.fn(),
			onCancel: vi.fn()
		});

		const cancelButton = Array.from(dialog()!.querySelectorAll('button')).find((b) =>
			(b.textContent || '').includes('Cancel')
		);
		expect(cancelButton).toBeTruthy();
	});

	it('backdrop click calls onCancel', () => {
		const onCancel = vi.fn();
		mountModal({ mountBody: () => {}, onConfirm: vi.fn(), onCancel });

		const backdrop = document.querySelector('[aria-label="Close modal"]') as HTMLElement;
		expect(backdrop).toBeTruthy();
		backdrop.click();
		expect(onCancel).toHaveBeenCalledTimes(1);
	});

	it('a prop update swaps the callback: Esc uses the newly set onCancel, not the one from mount', () => {
		const first = vi.fn();
		const second = vi.fn();
		mountModal({ mountBody: () => {}, onConfirm: vi.fn(), onCancel: first });

		component!.$set({ onCancel: second });
		pressKey('Escape');

		expect(first).not.toHaveBeenCalled();
		expect(second).toHaveBeenCalledTimes(1);
	});

	it('a host that only mounts/unmounts (isOpen stays at its default) can still close after an earlier Confirm settled the gate', async () => {
		const submit = vi.fn();
		const closeModal = vi.fn();
		mountModal({
			confirmLabel: 'Fetch prompts',
			mountBody: () => {},
			onConfirm: submit,
			onCancel: closeModal
		});

		const fetchButton = Array.from(dialog()!.querySelectorAll('button')).find((b) =>
			(b.textContent || '').includes('Fetch prompts')
		) as HTMLButtonElement;
		fetchButton.click();
		expect(submit).toHaveBeenCalledTimes(1);

		component!.$set({ busy: true });
		await wait(0);
		component!.$set({
			confirmLabel: 'Close',
			busy: false,
			confirmDisabled: false,
			hideCancel: true,
			onConfirm: closeModal
		});
		await wait(0);

		const closeButton = Array.from(dialog()!.querySelectorAll('button')).find((b) =>
			(b.textContent || '').includes('Close')
		) as HTMLButtonElement;
		expect(closeButton).toBeTruthy();
		closeButton.click();

		expect(closeModal).toHaveBeenCalledTimes(1);
	});

	it('Esc still closes after an earlier Confirm settled the gate, once busy clears', async () => {
		const submit = vi.fn();
		const closeModal = vi.fn();
		mountModal({
			confirmLabel: 'Fetch prompts',
			mountBody: () => {},
			onConfirm: submit,
			onCancel: closeModal
		});

		pressKey('Enter');
		expect(submit).toHaveBeenCalledTimes(1);

		component!.$set({ busy: true });
		await wait(0);
		component!.$set({ busy: false, hideCancel: true, confirmLabel: 'Close', onConfirm: closeModal });
		await wait(0);

		pressKey('Escape');
		expect(closeModal).toHaveBeenCalledTimes(1);
	});
});
