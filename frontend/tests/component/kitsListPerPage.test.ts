// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { makeKit } from '$lib/kits/fixtures';

const listKits = vi.fn();

vi.mock('$lib/services/api/index', () => ({ api: { listKits: (query: unknown) => listKits(query) } }));

const { default: KitsListPane } = await import('$lib/components/kits/KitsListPane.svelte');

const kits = Array.from({ length: 5 }, (_, index) => makeKit(`Kit ${index}`, `kit-${index}`, []));

let target: HTMLDivElement;
let mounted: ReturnType<typeof mount> | undefined;

async function settle() {
	for (let i = 0; i < 6; i++) {
		await Promise.resolve();
		flushSync();
	}
}

async function mountPane() {
	target = document.createElement('div');
	document.body.appendChild(target);
	mounted = mount(KitsListPane as never, {
		target,
		props: { onOpen: () => {}, onNew: () => {}, onPickFromLibrary: () => {} }
	});
	await settle();
}

beforeEach(() => {
	localStorage.clear();
	listKits.mockReset();
	listKits.mockResolvedValue({
		success: true,
		data: { kits, total: 60, page: 1, page_size: 24, labels: [] }
	});
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = undefined;
	document.body.innerHTML = '';
});

describe('Kits list per page', () => {
	it('asks for 24 kits per page by default', async () => {
		await mountPane();
		expect(listKits.mock.calls[0][0].page_size).toBe(24);
		expect(target.querySelector('[data-kit-per-page]')).not.toBeNull();
	});

	it('starts from the size the viewer chose last time', async () => {
		localStorage.setItem('kits-items-per-page', '48');
		await mountPane();
		expect(listKits.mock.calls.at(-1)?.[0].page_size).toBe(48);
	});

	it('ignores a stored size that is not one of the options', async () => {
		localStorage.setItem('kits-items-per-page', '7');
		await mountPane();
		expect(listKits.mock.calls.at(-1)?.[0].page_size).toBe(24);
	});

	it('goes back to page one and remembers the size when the viewer picks another', async () => {
		await mountPane();
		const trigger = target.querySelector('[data-kit-per-page] button, [data-kit-per-page] input') as HTMLElement;
		trigger.click();
		await settle();
		const option = Array.from(document.querySelectorAll('[role="option"]')).find((node) => node.textContent?.trim() === '96') as HTMLElement;
		option.click();
		await settle();
		const last = listKits.mock.calls.at(-1)?.[0];
		expect(last.page_size).toBe(96);
		expect(last.page).toBe(1);
		expect(localStorage.getItem('kits-items-per-page')).toBe('96');
	});
});
