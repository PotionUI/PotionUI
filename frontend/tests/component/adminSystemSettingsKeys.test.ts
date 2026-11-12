// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const hoisted = vi.hoisted(() => ({
	getSettings: vi.fn(),
	updateSettings: vi.fn(),
	getUsers: vi.fn(),
	getMcpCeiling: vi.fn(),
	patchMcpCeiling: vi.fn(),
	getUserApiAccess: vi.fn(),
	listAdminApiKeys: vi.fn(),
	toastError: vi.fn()
}));

vi.mock('$lib/services/admin-api', () => ({ getSettings: hoisted.getSettings, updateSettings: hoisted.updateSettings, getUsers: hoisted.getUsers }));
vi.mock('$lib/services/api/index', () => ({
	api: {
		getMcpCeiling: hoisted.getMcpCeiling,
		patchMcpCeiling: hoisted.patchMcpCeiling,
		getUserApiAccess: hoisted.getUserApiAccess,
		setUserApiAccess: vi.fn(),
		listAdminApiKeys: hoisted.listAdminApiKeys,
		revokeAdminApiKey: vi.fn(),
		listAdminApiKeyEvents: vi.fn()
	}
}));
vi.mock('$lib/stores/toast', () => ({ toasts: { success: vi.fn(), info: vi.fn(), error: hoisted.toastError } }));

const { default: SystemSettingsTab } = await import('../../src/routes/admin/components/SystemSettingsTab.svelte');
const { makeListing, makeTool } = await import('../../src/lib/components/apikeys/fixtures');
const pageStore = (await import('$app/stores')).page as unknown as { set: (value: unknown) => void };

let target: HTMLDivElement;
let instance: ReturnType<typeof mount> | null = null;

async function settle() {
	for (let i = 0; i < 12; i++) await new Promise((r) => setTimeout(r, 0));
	flushSync();
}

const settings = {
	registration_policy: 'closed',
	mcp_enabled: true,
	public_base_url: '',
	api_keys_enabled: true,
	api_key_scopes_allowed: ['read', 'generate', 'content', 'mcp'],
	api_key_max_lifetime_days: 0
};

const ceiling = () =>
	makeListing([makeTool({ name: 'search_gallery' }), makeTool({ name: 'start_generation', writes: true })], { level: 'ceiling', policy: 'all_on' });

async function open(view = 'access') {
	pageStore.set({ url: new URL(`http://localhost/admin?tab=settings&view=${view}`), params: {}, route: { id: null }, status: 200, error: null, data: {}, form: undefined, state: {} });
	target = document.createElement('div');
	target.style.height = '900px';
	document.body.appendChild(target);
	instance = mount(SystemSettingsTab, { target });
	await settle();
}

const byTestId = (id: string) => target.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
const saveButton = () => Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim() === 'Save')!;
const discardButton = () => Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim() === 'Discard')!;

beforeEach(() => {
	for (const fn of Object.values(hoisted)) fn.mockReset();
	hoisted.getSettings.mockResolvedValue({ success: true, data: { ...settings } });
	hoisted.updateSettings.mockResolvedValue({ success: true });
	hoisted.getMcpCeiling.mockResolvedValue({ success: true, data: ceiling() });
	hoisted.getUsers.mockResolvedValue({ success: true, data: [] });
	hoisted.listAdminApiKeys.mockResolvedValue({ success: true, data: { items: [], total: 0, page: 1, page_size: 10 } });
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target?.remove();
	document.body.innerHTML = '';
});

describe('System settings, Access', () => {
	it('puts a ceiling change on the same Save bar and sends it with the settings', async () => {
		await open();
		expect(saveButton().disabled).toBe(true);

		byTestId('api-keys-enabled').click();
		target.querySelector<HTMLElement>('[data-tool="search_gallery"] input[role="switch"]')!.click();
		await settle();
		expect(target.textContent).toContain('2 unsaved changes');
		expect(saveButton().disabled).toBe(false);

		hoisted.patchMcpCeiling.mockResolvedValue({ success: true, data: ceiling() });
		saveButton().click();
		await settle();
		expect(hoisted.updateSettings).toHaveBeenCalledTimes(1);
		expect(hoisted.updateSettings.mock.calls[0][0]).toMatchObject({ api_keys_enabled: false, api_key_scopes_allowed: ['read', 'generate', 'content', 'mcp'], api_key_max_lifetime_days: 0 });
		expect(hoisted.patchMcpCeiling).toHaveBeenCalledWith({ overrides: { search_gallery: false } });
		expect(target.textContent).not.toContain('unsaved change');
	});

	it('saves a ceiling change alone without writing settings', async () => {
		await open();
		target.querySelector<HTMLElement>('[data-tool="search_gallery"] input[role="switch"]')!.click();
		await settle();
		hoisted.patchMcpCeiling.mockResolvedValue({ success: true, data: ceiling() });
		saveButton().click();
		await settle();
		expect(hoisted.updateSettings).not.toHaveBeenCalled();
		expect(hoisted.patchMcpCeiling).toHaveBeenCalledTimes(1);
	});

	it('discards the settings and the ceiling together', async () => {
		await open();
		byTestId('scope-generate').click();
		target.querySelector<HTMLElement>('[data-tool="search_gallery"] input[role="switch"]')!.click();
		await settle();
		expect(target.textContent).toContain('2 unsaved changes');
		discardButton().click();
		await settle();
		expect(target.textContent).not.toContain('unsaved change');
		expect((byTestId('scope-generate') as HTMLInputElement).checked).toBe(true);
		expect(hoisted.patchMcpCeiling).not.toHaveBeenCalled();
		expect(hoisted.updateSettings).not.toHaveBeenCalled();
	});

	it('does not send the ceiling when the settings are refused', async () => {
		await open();
		byTestId('api-keys-enabled').click();
		target.querySelector<HTMLElement>('[data-tool="search_gallery"] input[role="switch"]')!.click();
		await settle();
		hoisted.updateSettings.mockResolvedValue({ success: false });
		saveButton().click();
		await settle();
		expect(hoisted.patchMcpCeiling).not.toHaveBeenCalled();
	});

	it('shows the all-keys table under API keys with no Save bar', async () => {
		await open('api_keys');
		expect(hoisted.listAdminApiKeys).toHaveBeenCalled();
		expect(byTestId('admin-api-keys')).not.toBeNull();
		expect(saveButton()).toBeUndefined();
	});
});
