// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { flushSync, mount, unmount } from 'svelte';
import { makeKit } from '$lib/kits/fixtures';

const listKits = vi.fn();

vi.mock('$lib/services/api/index', () => ({ api: { listKits: (query: unknown) => listKits(query) } }));

const { page } = await import('./stubs/appStores');
const { default: KitsListPane } = await import('$lib/components/kits/KitsListPane.svelte');

const kits = Array.from({ length: 5 }, (_, index) => makeKit(`Kit ${index}`, `kit-${index}`, []));

function setUrl(path: string) {
	page.update((current) => ({ ...current, url: new URL(`http://localhost${path}`) }));
}

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
	setUrl('/library?view=kits');
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

describe('Kits list state in the URL', () => {
	it('restores page, sort, search and label from the URL', async () => {
		setUrl('/library?view=kits&page=2&sort=name&dir=desc&q=mara&label=Faces');
		await mountPane();
		expect(listKits.mock.calls[0][0]).toMatchObject({ page: 2, sort_by: 'name', sort_dir: 'desc', search: 'mara', label: 'Faces' });
	});

	it('gives the defaults for a URL without list params and for junk values', async () => {
		await mountPane();
		expect(listKits.mock.calls[0][0]).toMatchObject({ page: 1, sort_by: 'manual', sort_dir: 'asc', search: '', label: null });
		unmount(mounted!);
		mounted = undefined;
		document.body.innerHTML = '';
		listKits.mockClear();
		setUrl('/library?view=kits&page=zzz&sort=bogus&dir=sideways');
		await mountPane();
		expect(listKits.mock.calls[0][0]).toMatchObject({ page: 1, sort_by: 'manual', sort_dir: 'asc' });
	});

	it('writes a page change to the URL with replaceState', async () => {
		await mountPane();
			const next = Array.from(target.querySelectorAll('nav[aria-label="Pagination"] button')).find((node) => node.textContent?.trim() === '2') as HTMLElement;
		next.click();
		await settle();
		expect(get(page).url.search).toBe('?view=kits&page=2');
	});

	it('steps back to the last page when the URL page is past the end', async () => {
		setUrl('/library?view=kits&page=9');
		await mountPane();
		expect(listKits.mock.calls.at(-1)?.[0].page).toBe(3);
	});
});
