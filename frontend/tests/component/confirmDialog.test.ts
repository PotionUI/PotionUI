// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync, createRawSnippet } from 'svelte';
import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
import ResetFixture from './fixtures/ConfirmDialogResetFixture.svelte';

let instance: ReturnType<typeof mount> | null = null;

const body = createRawSnippet(() => ({
	render: () => '<div><input data-testid="field" /><button data-testid="inner">Inner</button></div>'
}));

function mountDialog(props: Record<string, unknown> = {}) {
	const onConfirm = vi.fn();
	const onCancel = vi.fn();
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(ConfirmDialog, {
		target,
		props: { isOpen: true, confirmLabel: 'Go', hideCloseButton: true, onConfirm, onCancel, children: body, ...props }
	});
	flushSync();
	return { onConfirm, onCancel, instance: instance as unknown as { confirm(): boolean; cancel(): boolean } };
}

function press(key: string, init: KeyboardEventInit = {}, target: EventTarget = document.body) {
	const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
	target.dispatchEvent(event);
	flushSync();
	return event;
}

function buttonByText(text: string) {
	return [...document.querySelectorAll('button')].find((b) => b.textContent?.includes(text)) as HTMLButtonElement;
}

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

describe('ConfirmDialog', () => {
	it('confirms once on Enter and suppresses the default', () => {
		const { onConfirm } = mountDialog();
		const event = press('Enter');
		expect(onConfirm).toHaveBeenCalledTimes(1);
		expect(event.defaultPrevented).toBe(true);
	});

	it('settles once when a click races Enter', () => {
		const { onConfirm } = mountDialog();
		press('Enter');
		buttonByText('Go').click();
		flushSync();
		expect(onConfirm).toHaveBeenCalledTimes(1);
	});

	it('ignores Enter inside an input and on a button', () => {
		const { onConfirm } = mountDialog();
		press('Enter', {}, document.querySelector('[data-testid="field"]')!);
		press('Enter', {}, document.querySelector('[data-testid="inner"]')!);
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('submits on Enter in a single-line input only when the dialog opts in', () => {
		const field = () => document.querySelector('[data-testid="field"]')!;
		const off = mountDialog();
		press('Enter', {}, field());
		expect(off.onConfirm).not.toHaveBeenCalled();
		unmount(instance!);
		document.body.innerHTML = '';

		const on = mountDialog({ submitOnInputEnter: true });
		const event = press('Enter', {}, field());
		expect(on.onConfirm).toHaveBeenCalledTimes(1);
		expect(event.defaultPrevented).toBe(true);
	});

	it('with the opt-in, still leaves Enter alone in a textarea, a passthrough input, a button, and respects confirmDisabled', () => {
		const area = createRawSnippet(() => ({
			render: () =>
				'<div><textarea data-testid="area"></textarea><input data-testid="search" data-enter-passthrough /><button data-testid="inner">Inner</button></div>'
		}));
		const { onConfirm } = mountDialog({ submitOnInputEnter: true, children: area });
		press('Enter', {}, document.querySelector('[data-testid="area"]')!);
		press('Enter', {}, document.querySelector('[data-testid="search"]')!);
		press('Enter', {}, document.querySelector('[data-testid="inner"]')!);
		expect(onConfirm).not.toHaveBeenCalled();
		unmount(instance!);
		document.body.innerHTML = '';

		const disabled = mountDialog({ submitOnInputEnter: true, confirmDisabled: true, children: body });
		press('Enter', {}, document.querySelector('[data-testid="field"]')!);
		expect(disabled.onConfirm).not.toHaveBeenCalled();
	});

	it('ignores repeated Enter', () => {
		const { onConfirm } = mountDialog();
		press('Enter', { repeat: true });
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('does nothing while closed', () => {
		const { onConfirm, onCancel } = mountDialog({ isOpen: false });
		const enter = press('Enter');
		press('Escape');
		expect(enter.defaultPrevented).toBe(false);
		expect(onConfirm).not.toHaveBeenCalled();
		expect(onCancel).not.toHaveBeenCalled();
	});

	it('cancels once on Escape', () => {
		const { onCancel } = mountDialog();
		press('Escape');
		press('Escape');
		expect(onCancel).toHaveBeenCalledTimes(1);
	});

	it('leaves Escape alone when keyboard is off', () => {
		const { onCancel, onConfirm } = mountDialog({ keyboard: false });
		press('Escape');
		press('Enter');
		expect(onCancel).not.toHaveBeenCalled();
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('blocks keys and the backdrop while busy', () => {
		const { onConfirm, onCancel } = mountDialog({ busy: true });
		press('Enter');
		press('Escape');
		(document.querySelector('[aria-label="Close modal"]') as HTMLElement).click();
		flushSync();
		expect(onConfirm).not.toHaveBeenCalled();
		expect(onCancel).not.toHaveBeenCalled();
	});

	it('routes a backdrop click to cancel', () => {
		const { onCancel } = mountDialog();
		(document.querySelector('[aria-label="Close modal"]') as HTMLElement).click();
		flushSync();
		expect(onCancel).toHaveBeenCalledTimes(1);
	});

	it('keeps the gate open while confirm is disabled', () => {
		const { onConfirm } = mountDialog({ confirmDisabled: true });
		press('Enter');
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('recovers after confirm returns false', () => {
		const onConfirm = vi.fn().mockReturnValueOnce(false);
		mountDialog({ onConfirm });
		press('Enter');
		press('Enter');
		expect(onConfirm).toHaveBeenCalledTimes(2);
	});

	it('recovers after an async confirm resolves false', async () => {
		const onConfirm = vi.fn().mockResolvedValueOnce(false);
		mountDialog({ onConfirm });
		press('Enter');
		await Promise.resolve();
		await Promise.resolve();
		press('Enter');
		expect(onConfirm).toHaveBeenCalledTimes(2);
	});

	it('stays settled after an async confirm resolves true', async () => {
		const onConfirm = vi.fn().mockResolvedValue(true);
		mountDialog({ onConfirm });
		press('Enter');
		await Promise.resolve();
		await Promise.resolve();
		press('Enter');
		expect(onConfirm).toHaveBeenCalledTimes(1);
	});

	it('reopens the gate when resetKey changes', () => {
		const onConfirm = vi.fn();
		const target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(ResetFixture, { target, props: { onConfirm } });
		flushSync();
		press('Enter');
		(document.querySelector('[data-testid="bump"]') as HTMLElement).click();
		flushSync();
		press('Enter');
		expect(onConfirm).toHaveBeenCalledTimes(2);
	});

	it('exposes confirm and cancel', () => {
		const { onConfirm, onCancel, instance: dialog } = mountDialog();
		expect(dialog.confirm()).toBe(true);
		expect(dialog.cancel()).toBe(false);
		expect(onConfirm).toHaveBeenCalledTimes(1);
		expect(onCancel).not.toHaveBeenCalled();
	});

	it('recovers after onConfirm throws synchronously', () => {
		const onConfirm = vi
			.fn()
			.mockImplementationOnce(() => {
				throw new Error('boom');
			})
			.mockReturnValue(undefined);
		mountDialog({ onConfirm });
		press('Enter');
		press('Enter');
		expect(onConfirm).toHaveBeenCalledTimes(2);
	});

	it('recovers after onConfirm returns a rejected promise', async () => {
		const onConfirm = vi.fn().mockRejectedValueOnce(new Error('boom')).mockResolvedValue(true);
		mountDialog({ onConfirm });
		press('Enter');
		await Promise.resolve();
		await Promise.resolve();
		press('Enter');
		expect(onConfirm).toHaveBeenCalledTimes(2);
	});

	it('ignores Enter inside a textarea and a select', () => {
		const fields = createRawSnippet(() => ({
			render: () => '<div><textarea data-testid="ta"></textarea><select data-testid="sel"><option>a</option></select></div>'
		}));
		const { onConfirm } = mountDialog({ children: fields });
		press('Enter', {}, document.querySelector('[data-testid="ta"]')!);
		press('Enter', {}, document.querySelector('[data-testid="sel"]')!);
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('blocks Enter while the secondary action is busy', () => {
		const { onConfirm } = mountDialog({ secondaryBusy: true, secondaryLabel: 'Alt', onSecondary: vi.fn() });
		press('Enter');
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('renders a header icon snippet when given one', () => {
		const headerIconContent = createRawSnippet(() => ({ render: () => '<i data-testid="hdr-icon"></i>' }));
		mountDialog({ headerIconContent, title: 'Hello' });
		expect(document.querySelector('[data-testid="hdr-icon"]')).not.toBeNull();
		expect(document.body.textContent).toContain('Hello');
	});

	it('renders no header bar without title, header or close button', () => {
		mountDialog();
		expect(document.querySelector('.border-b.border-line')).toBeNull();
	});
});
