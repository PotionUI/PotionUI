// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { LIMITS, makeKey } from '../../src/lib/components/apikeys/fixtures';

const createApiKey = vi.fn();
const confirm = vi.fn();

vi.mock('$lib/services/api/index', async (importOriginal) => {
	const original = await importOriginal<typeof import('$lib/services/api/index')>();
	(original.api as Record<string, unknown>).createApiKey = (...args: unknown[]) => createApiKey(...args);
	return original;
});
vi.mock('$lib/stores/confirm', () => ({ confirmDialog: (...args: unknown[]) => confirm(...args) }));

const { default: CreateKeyDialog } = await import('../../src/lib/components/apikeys/CreateKeyDialog.svelte');

let instance: ReturnType<typeof mount> | null = null;

async function settle() {
	for (let i = 0; i < 5; i++) {
		await Promise.resolve();
		flushSync();
	}
}

const escape = () => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));

async function openSecretDialog(onClose: () => void) {
	createApiKey.mockResolvedValue({ success: true, data: { key: makeKey(), secret: 'pui_secret_value' } });
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(CreateKeyDialog, { target, props: { ctx: { isAdmin: false, limits: LIMITS }, onClose, onCreated: vi.fn() } });
	flushSync();
	const name = document.querySelector<HTMLInputElement>('#api-key-name')!;
	name.value = 'Batch';
	name.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('Create key'))!.click();
	await settle();
	expect(document.body.textContent).toContain('pui_secret_value');
}

beforeEach(() => {
	createApiKey.mockReset();
	confirm.mockReset();
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

describe('the key-shown-once dialog', () => {
	it('does not close on Escape until the user confirms leaving without copying', async () => {
		const onClose = vi.fn();
		await openSecretDialog(onClose);
		confirm.mockResolvedValue(false);
		escape();
		await settle();
		expect(confirm).toHaveBeenCalledTimes(1);
		expect(onClose).not.toHaveBeenCalled();
		expect(document.body.textContent).toContain('pui_secret_value');

		confirm.mockResolvedValue(true);
		escape();
		await settle();
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it('closes without asking once the key was copied and saved', async () => {
		const onClose = vi.fn();
		await openSecretDialog(onClose);
		Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('Copy key'))!.click();
		await settle();
		Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('I have saved it'))!.click();
		await settle();
		expect(confirm).not.toHaveBeenCalled();
		expect(onClose).toHaveBeenCalledTimes(1);
	});
});
