// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { writable } from 'svelte/store';
import { settle } from './helpers/settle';

const hoisted = vi.hoisted(() => ({
	posts: [] as { url: string; body: any }[],
	gets: [] as string[],
	installError: null as null | { status: number; message: string },
	installResult: null as null | Record<string, unknown>,
	confirmAnswer: true,
	confirmCalls: [] as any[]
}));

const authState = writable<{ user: { account_type: string } | null }>({ user: { account_type: 'ADMIN' } });

vi.mock('$lib/stores/auth', () => ({ authStore: { subscribe: authState.subscribe } }));

vi.mock('$lib/stores/confirm', async (orig) => ({
	...(await orig<typeof import('$lib/stores/confirm')>()),
	confirmDialog: vi.fn(async (options: unknown) => {
		hoisted.confirmCalls.push(options);
		return hoisted.confirmAnswer;
	})
}));

const CATALOG = {
	url: 'https://example.com/plugins.json',
	entries: [
		{ id: 'demo', name: 'Demo', version: '1.0.0', description: 'A demo plugin', license: 'MIT', requires_core: '0.1.0', archive: 'https://example.com/demo-1.0.0.zip', sha256: 'a'.repeat(64), status: 'available', reason: '', installed_version: null },
		{ id: 'old', name: 'Old', version: '2.0.0', description: '', license: '', requires_core: '0.1.0', archive: 'https://example.com/old-2.0.0.zip', sha256: 'b'.repeat(64), status: 'update', reason: '', installed_version: '1.0.0' },
		{ id: 'later', name: 'Later', version: '1.0.0', description: '', license: '', requires_core: '9.0.0', archive: 'https://example.com/later-1.0.0.zip', sha256: 'c'.repeat(64), status: 'incompatible', reason: 'This version cannot run here: it needs PotionUI >=9.0.0, this is 0.1.0.', installed_version: null },
		{ id: 'model-browser', name: 'Model Browser', version: '1.0.0', description: '', license: '', requires_core: '0.1.0', archive: 'https://example.com/mb.zip', sha256: 'd'.repeat(64), status: 'bundled', reason: 'PotionUI ships this plugin; a catalog copy would hide it.', installed_version: '1.0.0' }
	],
	problems: [{ index: 4, plugin_id: '../escape', reason: "id '../escape' is not a plugin folder name PotionUI installs" }]
};

vi.mock('$lib/services/api/index', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api/index')>('$lib/services/api/index');
	return {
		...actual,
		api: {
			...actual.api,
			getClient: () => ({
				get: vi.fn(async (url: string) => {
					hoisted.gets.push(url);
					if (url === '/api/plugins') return { data: { success: true, data: [] } };
					if (url === '/api/plugins/hooks/frontend') return { data: { success: true, data: {} } };
					if (url === '/api/plugins/frontend-extensions') {
						return { data: { success: true, data: { renderers: [], contributions: [], revisions: {} } } };
					}
					if (url === '/api/plugins/setup') return { data: { success: true, data: [] } };
					return { data: { success: true, data: [] } };
				}),
				post: vi.fn(async (url: string, body: any) => {
					hoisted.posts.push({ url, body });
					if (url === '/api/plugins/catalog') return { data: { success: true, data: CATALOG } };
					if (url === '/api/plugins/catalog/install') {
						if (hoisted.installError) {
							throw { response: { status: hoisted.installError.status, data: { detail: { error: 'plugin_install_refused', message: hoisted.installError.message } } } };
						}
						return {
							data: {
								success: true,
								data: hoisted.installResult ?? {
									plugin_id: body.plugin_id,
									version: '1.0.0',
									previous_version: null,
									enabled: false,
									dependencies_python: ['numpy>=1.24'],
									needs_restart: false
								}
							}
						};
					}
					return { data: { success: true } };
				})
			})
		}
	};
});

const { default: PluginCatalogModal } = await import('../../src/routes/admin/components/plugins/PluginCatalogModal.svelte');
const { default: PluginsTab } = await import('../../src/routes/admin/components/PluginsTab.svelte');

let mounted: Record<string, any> | null = null;
const onInstalled = vi.fn();

function openModal() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	mounted = mount(PluginCatalogModal, { target, props: { isOpen: true, onClose: () => {}, onInstalled } });
}

function button(label: string, root: ParentNode = document.body): HTMLButtonElement | undefined {
	return Array.from(root.querySelectorAll('button')).find((el) => el.textContent?.trim() === label) as HTMLButtonElement | undefined;
}

function entryRow(id: string): HTMLElement {
	return document.body.querySelector(`[data-plugin-id="${id}"]`) as HTMLElement;
}

async function readCatalog(url = 'https://example.com/plugins.json') {
	const input = document.body.querySelector('input[aria-label="Catalog URL"]') as HTMLInputElement;
	input.value = url;
	input.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
	button('Read catalog')!.click();
	await settle();
	flushSync();
}

beforeEach(() => {
	localStorage.clear();
	hoisted.posts.length = 0;
	hoisted.gets.length = 0;
	hoisted.confirmCalls.length = 0;
	hoisted.installError = null;
	hoisted.installResult = null;
	hoisted.confirmAnswer = true;
	onInstalled.mockReset();
	authState.set({ user: { account_type: 'ADMIN' } });
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	document.body.innerHTML = '';
});

describe('the catalog install window', () => {
	it('lists each catalog entry with its status and offers an action only where one applies', async () => {
		openModal();
		await readCatalog();

		expect(hoisted.posts).toEqual([{ url: '/api/plugins/catalog', body: { url: 'https://example.com/plugins.json' } }]);
		expect(entryRow('demo').textContent).toContain('Available');
		expect(button('Install', entryRow('demo'))).toBeTruthy();
		expect(button('Update to 2.0.0', entryRow('old'))).toBeTruthy();
		expect(entryRow('later').textContent).toContain('needs PotionUI >=9.0.0');
		expect(entryRow('later').querySelector('button')).toBeNull();
		expect(entryRow('model-browser').querySelector('button')).toBeNull();
		expect(document.body.textContent).toContain("id '../escape' is not a plugin folder name");
	});

	it('remembers the catalog it read', async () => {
		openModal();
		await readCatalog('https://example.com/other.json');
		unmount(mounted!);
		mounted = null;

		openModal();
		const input = document.body.querySelector('input[aria-label="Catalog URL"]') as HTMLInputElement;
		expect(input.value).toBe('https://example.com/other.json');
	});

	it('installs after the admin confirms, sending the version and sha256 the window showed', async () => {
		openModal();
		await readCatalog();

		button('Install', entryRow('demo'))!.click();
		await settle();
		flushSync();

		expect(hoisted.confirmCalls[0].title).toContain('Demo 1.0.0');
		expect(hoisted.confirmCalls[0].message).toContain('a'.repeat(64));
		expect(hoisted.confirmCalls[0].message).toContain('another version or sha256 by then, nothing is installed');
		expect(hoisted.confirmCalls[0].message).toContain('same rights as PotionUI');
		expect(hoisted.posts[1]).toEqual({
			url: '/api/plugins/catalog/install',
			body: { url: 'https://example.com/plugins.json', plugin_id: 'demo', version: '1.0.0', sha256: 'a'.repeat(64) }
		});
		expect(onInstalled).toHaveBeenCalledWith(expect.objectContaining({ plugin_id: 'demo', version: '1.0.0' }));
		expect(document.body.textContent).toContain('Plugin installed');
		expect(document.body.textContent).toContain('numpy>=1.24');
		expect(hoisted.posts[2].url).toBe('/api/plugins/catalog');
	});

	async function update(result: Record<string, unknown>) {
		hoisted.installResult = { plugin_id: 'old', version: '2.0.0', previous_version: '1.0.0', dependencies_python: [], ...result };
		openModal();
		await readCatalog();

		button('Update to 2.0.0', entryRow('old'))!.click();
		await settle();
		flushSync();
		return document.body.textContent!.replace(/\s+/g, ' ');
	}

	it('asks for a restart after an update of a plugin whose old code the server had loaded', async () => {
		const text = await update({ enabled: true, needs_restart: true });

		expect(text).toContain('Plugin updated: restart PotionUI');
		expect(text).toContain(
			'old 1.0.0 → 2.0.0 is in place. PotionUI had loaded code from an earlier copy of old before this update. Restart PotionUI so that only the code of 2.0.0 is loaded.'
		);
		expect(text).not.toContain('is running');
	});

	it('asks for a restart after an update of a disabled plugin whose old code the server had loaded', async () => {
		const text = await update({ enabled: false, needs_restart: true });

		expect(text).toContain(
			'old 1.0.0 → 2.0.0 is in the list, disabled until you enable it. PotionUI had loaded code from an earlier copy of old before this update. Restart PotionUI so that only the code of 2.0.0 is loaded.'
		);
		expect(text).not.toContain('runs code');
	});

	it('asks for a restart after a first install over a copy whose code the server had loaded, without calling it an update', async () => {
		hoisted.installResult = {
			plugin_id: 'demo',
			version: '1.0.0',
			previous_version: null,
			enabled: false,
			dependencies_python: [],
			needs_restart: true
		};
		openModal();
		await readCatalog();

		button('Install', entryRow('demo'))!.click();
		await settle();
		flushSync();
		const text = document.body.textContent!.replace(/\s+/g, ' ');

		expect(text).toContain('Plugin installed: restart PotionUI');
		expect(text).toContain(
			'demo 1.0.0 is in the list, disabled until you enable it. PotionUI had loaded code from an earlier copy of demo before this install. Restart PotionUI so that only the code of 1.0.0 is loaded.'
		);
		expect(text).not.toContain('Plugin updated');
		expect(text).not.toContain('this update');
	});

	it('names no version for the loaded code after a second update without a restart', async () => {
		const text = await update({ previous_version: '1.1.0', enabled: false, needs_restart: true });

		expect(text).toContain(
			'old 1.1.0 → 2.0.0 is in the list, disabled until you enable it. PotionUI had loaded code from an earlier copy of old before this update.'
		);
		expect(text).not.toContain('code from 1.1.0');
	});

	it('says an update is running when the server needs no restart for it', async () => {
		const text = await update({ enabled: true, needs_restart: false });

		expect(text).toContain('Plugin updated');
		expect(text).toContain('old 1.0.0 → 2.0.0 is running.');
		expect(text).not.toContain('Restart PotionUI');
	});

	it('sends nothing when the admin declines', async () => {
		hoisted.confirmAnswer = false;
		openModal();
		await readCatalog();

		button('Install', entryRow('demo'))!.click();
		await settle();

		expect(hoisted.posts.map((post) => post.url)).toEqual(['/api/plugins/catalog']);
		expect(onInstalled).not.toHaveBeenCalled();
	});

	it('shows why the server refused an install', async () => {
		hoisted.installError = { status: 400, message: 'The archive sha256 differs from the catalog; nothing was installed.' };
		openModal();
		await readCatalog();

		button('Install', entryRow('demo'))!.click();
		await settle();
		flushSync();

		expect(document.body.textContent).toContain('The archive sha256 differs from the catalog; nothing was installed.');
		expect(onInstalled).not.toHaveBeenCalled();
	});
});

describe('the Plugins tab', () => {
	async function mountTab() {
		const target = document.createElement('div');
		document.body.appendChild(target);
		mounted = mount(PluginsTab, { target, props: {} });
		await settle();
		flushSync();
	}

	it('offers the catalog install to an admin', async () => {
		await mountTab();

		expect(button('Install from catalog')).toBeTruthy();
	});

	it('does not offer it to another account', async () => {
		authState.set({ user: { account_type: 'USER' } });
		await mountTab();

		expect(button('Install from catalog')).toBeUndefined();
		expect(document.body.querySelector('input[aria-label="Catalog URL"]')).toBeNull();
	});
});
