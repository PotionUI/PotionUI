// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { writable } from 'svelte/store';

const bulkDeleteGenerations = vi.fn();
const toastError = vi.fn();

vi.mock('$lib/stores/history', () => {
	const state = writable({ selectedGenerationIds: ['g1'], generations: [{ id: 'g1' }] });
	return { historyStore: { subscribe: state.subscribe, bulkDeleteGenerations } };
});

vi.mock('$lib/stores/toast', () => ({
	toasts: { error: (...args: unknown[]) => toastError(...args), success: vi.fn(), info: vi.fn() }
}));

const { default: HistoryBulkDeleteModal } = await import(
	'../../src/routes/history/components/HistoryBulkDeleteModal.svelte'
);

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('HistoryBulkDeleteModal', () => {
	it('shows the backend reason when the delete is refused', async () => {
		const onClose = vi.fn();
		bulkDeleteGenerations.mockRejectedValue({
			message: 'Request failed with status code 400',
			response: { status: 400, data: { detail: { error: 'bulk_delete_blocked', message: 'Generation is used in an elixir' } } }
		});
		const target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(HistoryBulkDeleteModal, { target, props: { onClose } });
		await settle();

		const confirm = [...document.querySelectorAll('button')].find((b) => b.textContent?.includes('Delete'));
		confirm!.click();
		await settle();

		expect(toastError).toHaveBeenCalledWith('Generation is used in an elixir');
		expect(onClose).not.toHaveBeenCalled();
	});
});
