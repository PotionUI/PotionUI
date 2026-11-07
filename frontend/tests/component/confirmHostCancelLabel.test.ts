// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';

const { confirmDialog, cancelAllConfirms } = await import('$lib/stores/confirm');
const { default: ConfirmHost } = await import('$lib/components/modals/ConfirmHost.svelte');
const { createClassComponent } = await import('svelte/legacy');

let target: HTMLElement | undefined;
let component: { $destroy: () => void } | undefined;

function mountHost() {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({ component: ConfirmHost as never, target });
}

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function buttonLabels(): string[] {
	const dialog = document.body.querySelector('[role="alertdialog"]');
	return [...(dialog?.querySelectorAll('button') ?? [])].map((b) => (b.textContent ?? '').replace(/\s+/g, ' ').trim());
}

afterEach(async () => {
	cancelAllConfirms();
	component?.$destroy();
	component = undefined;
	target?.remove();
	await settle();
});

describe('ConfirmHost button labels', () => {
	it('shows the cancel label a caller passes and resolves false from that button', async () => {
		mountHost();
		const pending = confirmDialog({ message: 'Leave the editor?', confirmLabel: 'Leave', cancelLabel: 'Keep editing' });
		await settle();

		expect(buttonLabels().some((label) => label.startsWith('Keep editing'))).toBe(true);
		expect(buttonLabels().some((label) => label.startsWith('Cancel'))).toBe(false);

		const dialog = document.body.querySelector('[role="alertdialog"]')!;
		const cancel = [...dialog.querySelectorAll('button')].find((b) => (b.textContent ?? '').includes('Keep editing'))!;
		cancel.click();
		await expect(pending).resolves.toBe(false);
	});

	it('keeps Cancel when no cancel label is given', async () => {
		mountHost();
		void confirmDialog({ message: 'Delete this?' });
		await settle();
		expect(buttonLabels().some((label) => label.startsWith('Cancel'))).toBe(true);
	});
});
