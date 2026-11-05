import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const listElixirs = vi.fn();
const updateElixir = vi.fn();

vi.mock('$lib/services/api/elixirs', () => ({ elixirsApi: () => ({ listElixirs, updateElixir }) }));

import AllElixirsDialog from '$lib/components/elixirs/AllElixirsDialog.svelte';

let instance: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
	listElixirs.mockReset();
	updateElixir.mockReset();
});

function escape(target: Element) {
	const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
	target.dispatchEvent(event);
	flushSync();
}

async function startRename(onclose: () => void) {
	listElixirs.mockResolvedValue({ ok: true, data: { elixirs: [{ id: 'a', name: 'Portrait', status: 'idle', active_runs: 0, node_count: 2, revision: 1 }] } });
	instance = mount(AllElixirsDialog as never, { target: document.body, props: { onclose } });
	flushSync();
	await vi.waitFor(() => expect(document.body.querySelector('[data-testid="all-elixirs-row"]')).not.toBeNull());
	document.body.querySelector<HTMLButtonElement>('button[aria-label="Actions for Portrait"]')!.click();
	flushSync();
	const rename = [...document.body.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')].find((item) => item.textContent?.includes('Rename'))!;
	rename.click();
	flushSync();
	await vi.waitFor(() => expect(document.body.querySelector('input[aria-label="Rename Portrait"]')).not.toBeNull());
	return document.body.querySelector<HTMLInputElement>('input[aria-label="Rename Portrait"]')!;
}

describe('All elixirs rename', () => {
	it('Escape cancels the rename without saving or closing, and the next Escape closes the dialog', async () => {
		const onclose = vi.fn();
		const input = await startRename(onclose);
		input.value = 'Discarded name';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();

		escape(input);
		input.dispatchEvent(new FocusEvent('blur'));
		flushSync();

		expect(onclose).not.toHaveBeenCalled();
		expect(updateElixir).not.toHaveBeenCalled();
		expect(document.body.querySelector('input[aria-label="Rename Portrait"]')).toBeNull();
		expect(document.body.textContent).toContain('Portrait');

		escape(document.body);
		expect(onclose).toHaveBeenCalledTimes(1);
	});
});
