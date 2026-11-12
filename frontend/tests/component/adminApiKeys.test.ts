// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const hoisted = vi.hoisted(() => ({
	listAdminApiKeys: vi.fn(),
	revokeAdminApiKey: vi.fn(),
	listAdminApiKeyEvents: vi.fn(),
	getUserApiAccess: vi.fn(),
	setUserApiAccess: vi.fn(),
	getMcpCeiling: vi.fn(),
	patchMcpCeiling: vi.fn(),
	getUsers: vi.fn(),
	confirmDialog: vi.fn(),
	toastSuccess: vi.fn(),
	toastInfo: vi.fn(),
	toastError: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({
	api: {
		listAdminApiKeys: hoisted.listAdminApiKeys,
		revokeAdminApiKey: hoisted.revokeAdminApiKey,
		listAdminApiKeyEvents: hoisted.listAdminApiKeyEvents,
		getUserApiAccess: hoisted.getUserApiAccess,
		setUserApiAccess: hoisted.setUserApiAccess,
		getMcpCeiling: hoisted.getMcpCeiling,
		patchMcpCeiling: hoisted.patchMcpCeiling
	}
}));
vi.mock('$lib/services/admin-api', () => ({ getUsers: hoisted.getUsers }));
vi.mock('$lib/stores/confirm', () => ({ confirmDialog: hoisted.confirmDialog }));
vi.mock('$lib/stores/toast', () => ({ toasts: { success: hoisted.toastSuccess, info: hoisted.toastInfo, error: hoisted.toastError } }));

const { default: AdminApiKeysPanel } = await import('../../src/routes/admin/components/apikeys/AdminApiKeysPanel.svelte');
const { default: ApiKeysAccessSection } = await import('../../src/routes/admin/components/apikeys/ApiKeysAccessSection.svelte');
const { default: McpCeilingSection } = await import('../../src/routes/admin/components/apikeys/McpCeilingSection.svelte');
const { default: UserApiAccessSection } = await import('../../src/routes/admin/components/apikeys/UserApiAccessSection.svelte');
const { default: PersonAccessSection } = await import('../../src/routes/admin/components/apikeys/PersonAccessSection.svelte');
const { CeilingDraft } = await import('../../src/routes/admin/components/apikeys/ceilingDraft.svelte');
const { makeAdminKey, makeListing, makeTool } = await import('../../src/lib/components/apikeys/fixtures');

let target: HTMLDivElement;
let instance: ReturnType<typeof mount> | null = null;

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
	flushSync();
}

async function render(component: any, props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(component, { target, props });
	await settle();
}

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target?.remove();
	document.body.innerHTML = '';
});

beforeEach(() => {
	for (const fn of Object.values(hoisted)) fn.mockReset();
	hoisted.getUsers.mockResolvedValue({
		success: true,
		data: [
			{ id: 'u1', username: 'Marta Kowalska', email: 'marta@example.com' },
			{ id: 'u2', username: 'Priya Nair', email: 'priya@example.com' }
		]
	});
});

const button = (root: ParentNode, text: string) =>
	Array.from(root.querySelectorAll<HTMLButtonElement>('button')).find(
		(b) => b.textContent?.replace(/\s+/g, ' ').trim().replace(/ \d+$/, '') === text || b.getAttribute('aria-label') === text
	)!;

const click = async (el: HTMLElement) => {
	el.click();
	await settle();
};

function keysPage(items = [makeAdminKey({ id: 'a', name: 'Home Assistant' }), makeAdminKey({ id: 'b', name: 'Raycast', owner: { id: 'u2', username: 'Priya Nair' } })], total = items.length, page = 1, counts: Record<string, number> | undefined = undefined) {
	return { success: true, data: { items, total, page, page_size: 10, counts: counts ?? { all: total, active: total, expired: 0, revoked: 0 } } };
}

describe('AdminApiKeysPanel', () => {
	it('asks for the first page of every status and lists owner, key and what it can do', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage());
		await render(AdminApiKeysPanel);
		expect(hoisted.listAdminApiKeys).toHaveBeenCalledWith({ status: 'all', userId: null, query: '', page: 1, pageSize: 10 });
		const row = target.querySelector('[data-admin-key="a"]')!;
		expect(row.textContent).toContain('Marta Kowalska');
		expect(row.textContent).toContain('Home Assistant');
		expect(row.textContent).toContain('pui_7Hq2xK9a…Qz0P');
		expect(row.textContent).toContain('Read, MCP');
		expect(target.querySelector('[data-testid="admin-keys-range"]')!.textContent).toContain('1–2 of 2');
	});

	it('sends the status filter to the query and goes back to page 1', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage(undefined, 35));
		await render(AdminApiKeysPanel);
		await click(button(target, 'Page 3'));
		expect(hoisted.listAdminApiKeys).toHaveBeenLastCalledWith({ status: 'all', userId: null, query: '', page: 3, pageSize: 10 });
		await click(button(target, 'Revoked'));
		expect(hoisted.listAdminApiKeys).toHaveBeenLastCalledWith({ status: 'revoked', userId: null, query: '', page: 1, pageSize: 10 });
	});

	it('pages with the numeric pager and shows no pager for one page', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage());
		await render(AdminApiKeysPanel);
		expect(target.querySelector('nav[aria-label="Pagination"]')).toBeNull();
		unmount(instance!);
		target.remove();
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage(undefined, 47));
		await render(AdminApiKeysPanel);
		expect(target.querySelector('nav[aria-label="Pagination"]')).not.toBeNull();
		await click(button(target, 'Next page'));
		expect(hoisted.listAdminApiKeys).toHaveBeenLastCalledWith({ status: 'all', userId: null, query: '', page: 2, pageSize: 10 });
	});

	it('sends the owner to the query', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage());
		await render(AdminApiKeysPanel);
		await click(target.querySelector<HTMLElement>('[data-testid="owner-filter"]')!);
		await click(document.body.querySelector<HTMLElement>('[data-owner-option="u2"]')!);
		expect(hoisted.listAdminApiKeys).toHaveBeenLastCalledWith({ status: 'all', userId: 'u2', query: '', page: 1, pageSize: 10 });
		expect(target.querySelector('[data-testid="owner-filter"]')!.textContent).toContain('Priya Nair');
	});

	it('searches every key on the server after a short pause and returns to page 1', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage(undefined, 47));
		await render(AdminApiKeysPanel);
		await click(button(target, 'Page 3'));
		const input = target.querySelector<HTMLInputElement>('input[type="search"]')!;
		expect(input.placeholder).toBe('Search keys');
		expect(input.className).toContain('input');
		input.value = 'raycast';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.value = 'raycast bot';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		const searches = hoisted.listAdminApiKeys.mock.calls.filter((call) => call[0].query);
		expect(searches).toHaveLength(0);
		await new Promise((r) => setTimeout(r, 320));
		await settle();
		const sent = hoisted.listAdminApiKeys.mock.calls.filter((call) => call[0].query);
		expect(sent).toHaveLength(1);
		expect(sent[0][0]).toEqual({ status: 'all', userId: null, query: 'raycast bot', page: 1, pageSize: 10 });
	});

	it('shows the count of every status on its chip', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage(undefined, 47, 1, { all: 47, active: 34, expired: 8, revoked: 5 }));
		await render(AdminApiKeysPanel);
		const group = target.querySelector('[role="group"][aria-label="Key status"]')!;
		const text = (label: string) => button(group, label.split(' ')[0]).textContent!.replace(/\s+/g, ' ').trim();
		expect(text('All')).toBe('All 47');
		expect(text('Active')).toBe('Active 34');
		expect(text('Expired')).toBe('Expired 8');
		expect(text('Revoked')).toBe('Revoked 5');
	});

	it('revokes another user\'s key only after the confirm names the owner', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage());
		hoisted.confirmDialog.mockResolvedValue(false);
		await render(AdminApiKeysPanel);
		await click(target.querySelector<HTMLElement>('[data-admin-key="a"] button[aria-label="Revoke key"]')!);
		const options = hoisted.confirmDialog.mock.calls[0][0];
		expect(options.title).toBe('Revoke “Home Assistant” for Marta Kowalska?');
		expect(options.variant).toBe('danger');
		expect(hoisted.revokeAdminApiKey).not.toHaveBeenCalled();

		hoisted.confirmDialog.mockResolvedValue(true);
		hoisted.revokeAdminApiKey.mockResolvedValue({ success: true, data: { id: 'a', revoked: true } });
		await click(target.querySelector<HTMLElement>('[data-admin-key="a"] button[aria-label="Revoke key"]')!);
		expect(hoisted.revokeAdminApiKey).toHaveBeenCalledWith('a');
		expect(hoisted.listAdminApiKeys).toHaveBeenCalledTimes(2);
		expect(hoisted.toastSuccess).toHaveBeenCalledWith('Key revoked');
	});

	it('offers no revoke on a key that is already revoked', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage([makeAdminKey({ id: 'r', status: 'revoked' })]));
		await render(AdminApiKeysPanel);
		expect(target.querySelector('[data-admin-key="r"] button[aria-label="Revoke key"]')).toBeNull();
		expect(target.querySelector('[data-admin-key="r"]')!.textContent).toContain('Revoked');
	});

	it('opens the history of a key and says who revoked it', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage([makeAdminKey({ id: 'r', status: 'revoked', name: 'Home Assistant' })]));
		hoisted.listAdminApiKeyEvents.mockResolvedValue({
			success: true,
			data: [
				{ action: 'created', actor: { id: 'u1', username: 'Marta Kowalska' }, detail: {}, changes: null, created_at: '2026-08-20T10:00:00Z' },
				{ action: 'revoked', actor: { id: 'a1', username: 'Priya Nair' }, detail: { by_owner: false }, changes: null, created_at: '2026-11-09T10:00:00Z' }
			]
		});
		await render(AdminApiKeysPanel);
		await click(target.querySelector<HTMLElement>('[data-admin-key="r"] > button')!);
		expect(hoisted.listAdminApiKeyEvents).toHaveBeenCalledWith('r');
		const history = document.body.querySelector('[data-testid="admin-key-history"]')!;
		expect(history.textContent).toContain('By administrator Priya Nair');
		expect(history.querySelectorAll('li')[0].textContent).toContain('Revoked');
		await click(button(document.body, 'Close'));
		expect(document.body.querySelector('[data-testid="admin-key-detail"]')).toBeNull();
	});

	it('flags an administer key that never expires and leaves an ordinary never-expiring key alone', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(
			keysPage([
				makeAdminKey({ id: 'adm', name: 'Ops automation', scopes: ['read', 'admin'], expires_at: null }),
				makeAdminKey({ id: 'ord', name: 'Raycast', scopes: ['read'], expires_at: null }),
				makeAdminKey({ id: 'cap', name: 'Capped', scopes: ['admin'], expires_at: '2027-02-09T09:00:00Z' })
			])
		);
		hoisted.listAdminApiKeyEvents.mockResolvedValue({ success: true, data: [] });
		await render(AdminApiKeysPanel);
		const adm = target.querySelector('[data-admin-key="adm"]')!;
		expect(adm.textContent).toContain('No expiry');
		expect(adm.textContent).not.toContain('Never');
		expect(adm.querySelector('[data-expires]')!.getAttribute('data-expires')).toBe('uncapped');
		expect(target.querySelector('[data-admin-key="ord"]')!.textContent).toContain('Never');
		expect(target.querySelector('[data-admin-key="ord"]')!.textContent).not.toContain('No expiry');
		expect(target.querySelector('[data-admin-key="cap"]')!.textContent).toContain('Feb 9, 2027');
		await click(target.querySelector<HTMLElement>('[data-admin-key="adm"] > button')!);
		expect(document.body.querySelector('[data-testid="admin-no-expiry-note"]')!.textContent).toContain('should expire within 90 days');
	});

	it('says so when there are no keys and when a filter matches none', async () => {
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage([], 0));
		await render(AdminApiKeysPanel);
		expect(target.textContent).toContain('No keys yet');
		await click(button(target, 'Revoked'));
		expect(target.textContent).toContain('No keys match');
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage());
		await click(button(target, 'Clear filters'));
		expect(hoisted.listAdminApiKeys).toHaveBeenLastCalledWith({ status: 'all', userId: null, query: '', page: 1, pageSize: 10 });
	});
});

describe('ApiKeysAccessSection', () => {
	const settings = { api_keys_enabled: true, mcp_enabled: true, api_key_scopes_allowed: ['read', 'generate', 'content', 'mcp'], api_key_max_lifetime_days: 0 };
	const props = (over: Record<string, unknown> = {}, onSettingChange = vi.fn()) => ({
		settings: { ...settings, ...over },
		savedSnapshot: JSON.stringify(settings),
		onSettingChange
	});
	const input = (id: string) => target.querySelector<HTMLInputElement>(`[data-testid="${id}"]`)!;

	it('turns a scope off by sending the whole list without it', async () => {
		const onSettingChange = vi.fn();
		await render(ApiKeysAccessSection, props({}, onSettingChange));
		await click(input('scope-generate'));
		expect(onSettingChange).toHaveBeenCalledWith('api_key_scopes_allowed', ['read', 'content', 'mcp']);
	});

	it('turns Administer on and warns that admin keys can install plugins', async () => {
		const onSettingChange = vi.fn();
		await render(ApiKeysAccessSection, props({}, onSettingChange));
		expect(target.textContent).toContain('Admin keys can install plugins. They last 90 days at most.');
		await click(input('scope-admin'));
		expect(onSettingChange).toHaveBeenCalledWith('api_key_scopes_allowed', ['read', 'generate', 'content', 'admin', 'mcp']);
	});

	it('chooses the longest key lifetime in days', async () => {
		const onSettingChange = vi.fn();
		await render(ApiKeysAccessSection, props({}, onSettingChange));
		await click(button(target, '90 days'));
		expect(onSettingChange).toHaveBeenCalledWith('api_key_max_lifetime_days', 90);
		await click(button(target, 'No limit'));
		expect(onSettingChange).toHaveBeenCalledWith('api_key_max_lifetime_days', 0);
	});

	it('says what happens to existing keys when API keys are switched off, and nothing while on', async () => {
		const onSettingChange = vi.fn();
		await render(ApiKeysAccessSection, props({}, onSettingChange));
		expect(target.querySelector('[data-testid="api-keys-enabled-note"]')).toBeNull();
		await click(input('api-keys-enabled'));
		expect(onSettingChange).toHaveBeenCalledWith('api_keys_enabled', false);
		unmount(instance!);
		target.remove();
		await render(ApiKeysAccessSection, props({ api_keys_enabled: false }));
		const note = target.querySelector('[data-testid="api-keys-enabled-note"]')!.textContent!;
		expect(note).toContain('within seconds');
		expect(note).toContain('open connections');
		expect(note).toContain('not deleted');
	});

	it('warns that MCP clients are cut off when MCP is switched off', async () => {
		await render(ApiKeysAccessSection, props({ mcp_enabled: false }));
		const note = target.querySelector('[data-testid="mcp-enabled-note"]')!.textContent!;
		expect(note).toContain('cut off within seconds');
		expect(note).toContain('HTTP API');
	});

	it('warns when a scope that was allowed is taken away', async () => {
		await render(ApiKeysAccessSection, props({ api_key_scopes_allowed: ['read', 'content', 'mcp'] }));
		expect(target.querySelector('[data-testid="scope-generate-note"]')!.textContent).toContain('lose it within seconds');
		expect(target.querySelector('[data-testid="scope-content-note"]')).toBeNull();
	});

	it('says new keys only for a lifetime limit', async () => {
		await render(ApiKeysAccessSection, props({ api_key_max_lifetime_days: 30 }));
		expect(target.querySelector('[data-testid="lifetime-note"]')!.textContent).toContain('Existing keys keep their expiry');
	});
});

describe('MCP tools ceiling', () => {
	const ceilingListing = () =>
		makeListing(
			[makeTool({ name: 'start_generation', writes: true }), makeTool({ name: 'search_gallery' }), makeTool({ name: 'run_media_tool', writes: true, state: 'off' })],
			{ level: 'ceiling', policy: 'all_on' }
		);

	async function open() {
		hoisted.getMcpCeiling.mockResolvedValue({ success: true, data: ceilingListing() });
		const ceiling = new CeilingDraft();
		await ceiling.load();
		await render(McpCeilingSection, { ceiling });
		return ceiling;
	}

	it('keeps a change as a draft until the page is saved, then sends one patch', async () => {
		const ceiling = await open();
		const row = target.querySelector<HTMLElement>('[data-tool="search_gallery"]')!;
		await click(row.querySelector<HTMLElement>('input[role="switch"]')!);
		expect(hoisted.patchMcpCeiling).not.toHaveBeenCalled();
		expect(ceiling.count).toBe(1);
		expect(target.textContent).toContain('1 of 3 allowed');
		expect(target.querySelector('[data-testid="mcp-ceiling-note"]')!.textContent).toContain('lose any tool turned off here within seconds');

		hoisted.patchMcpCeiling.mockResolvedValue({ success: true, data: { ...ceilingListing(), tools: ceilingListing().tools.map((t) => (t.name === 'search_gallery' ? { ...t, state: 'off' as const } : t)) } });
		expect(await ceiling.save()).toBe(true);
		expect(hoisted.patchMcpCeiling).toHaveBeenCalledWith({ overrides: { search_gallery: false } });
		expect(ceiling.count).toBe(0);
	});

	it('discards the draft', async () => {
		const ceiling = await open();
		await click(button(target, 'Read-only tools only'));
		expect(ceiling.listing?.policy).toBe('read_only_on');
		expect(ceiling.count).toBe(1);
		ceiling.discard();
		await settle();
		expect(ceiling.listing?.policy).toBe('all_on');
		expect(ceiling.count).toBe(0);
		expect(hoisted.patchMcpCeiling).not.toHaveBeenCalled();
	});

	it('sends nothing when a tool is switched off and on again', async () => {
		const ceiling = await open();
		const toggle = () => target.querySelector<HTMLElement>('[data-tool="search_gallery"] input[role="switch"]')!;
		await click(toggle());
		await click(toggle());
		expect(ceiling.count).toBe(0);
		expect(await ceiling.save()).toBe(true);
		expect(hoisted.patchMcpCeiling).not.toHaveBeenCalled();
	});

	it('keeps the draft and reports a failed save', async () => {
		const ceiling = await open();
		await click(button(target, 'Turn off writes'));
		hoisted.patchMcpCeiling.mockResolvedValue({ success: false, message: 'no' });
		expect(await ceiling.save()).toBe(false);
		expect(ceiling.count).toBe(1);
	});

	it('says the tool list is not available when the route is missing', async () => {
		hoisted.getMcpCeiling.mockRejectedValue({ response: { status: 404 } });
		const ceiling = new CeilingDraft();
		await ceiling.load();
		await render(McpCeilingSection, { ceiling });
		expect(target.textContent).toContain('MCP tools are not available yet.');
	});
});

describe('user API access', () => {
	const access = (over = {}) => ({ success: true, data: { keys_enabled: true, mcp_enabled: true, ...over } });

	it('turns API keys off for one user, keeps their MCP choice and says what happens', async () => {
		hoisted.getUserApiAccess.mockResolvedValue(access());
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage([makeAdminKey({ id: 'k1' })]));
		hoisted.setUserApiAccess.mockResolvedValue(access({ keys_enabled: false }));
		await render(UserApiAccessSection, { userId: 'u1', name: 'Marta Kowalska' });
		expect(hoisted.getUserApiAccess).toHaveBeenCalledWith('u1');
		await click(target.querySelector<HTMLElement>('[data-testid="access-keys-u1"]')!);
		expect(hoisted.setUserApiAccess).toHaveBeenCalledWith('u1', { keys_enabled: false, mcp_enabled: true });
		expect(hoisted.toastInfo.mock.calls[0][0]).toContain('stop working within seconds');
		expect(target.querySelector('[data-testid="user-access-note"]')!.textContent).toContain('Nothing is deleted');
		expect(target.querySelector<HTMLInputElement>('[data-testid="access-mcp-u1"]')!.disabled).toBe(true);
	});

	it('turns MCP off for one user and says their keys keep working over HTTP', async () => {
		hoisted.getUserApiAccess.mockResolvedValue(access());
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage([]));
		hoisted.setUserApiAccess.mockResolvedValue(access({ mcp_enabled: false }));
		await render(UserApiAccessSection, { userId: 'u1', name: 'Marta Kowalska' });
		await click(target.querySelector<HTMLElement>('[data-testid="access-mcp-u1"]')!);
		expect(hoisted.setUserApiAccess).toHaveBeenCalledWith('u1', { keys_enabled: true, mcp_enabled: false });
		expect(hoisted.toastInfo.mock.calls[0][0]).toContain('keys keep working over the HTTP API');
	});

	it('reloads the real state and reports when the change is refused', async () => {
		hoisted.getUserApiAccess.mockResolvedValue(access());
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage([]));
		hoisted.setUserApiAccess.mockRejectedValue({ response: { data: { message: 'User not found' } } });
		await render(UserApiAccessSection, { userId: 'u1', name: 'Marta Kowalska' });
		await click(target.querySelector<HTMLElement>('[data-testid="access-keys-u1"]')!);
		expect(hoisted.toastError).toHaveBeenCalled();
		expect(hoisted.getUserApiAccess).toHaveBeenCalledTimes(2);
		expect(target.querySelector<HTMLInputElement>('[data-testid="access-keys-u1"]')!.checked).toBe(true);
	});

	it('lists the user\'s keys read-only and revokes one after naming the owner', async () => {
		hoisted.getUserApiAccess.mockResolvedValue(access());
		hoisted.listAdminApiKeys.mockResolvedValue(keysPage([makeAdminKey({ id: 'k1', name: 'Raycast' }), makeAdminKey({ id: 'k2', status: 'revoked' })], 12));
		hoisted.confirmDialog.mockResolvedValue(true);
		hoisted.revokeAdminApiKey.mockResolvedValue({ success: true, data: { id: 'k1', revoked: true } });
		await render(UserApiAccessSection, { userId: 'u1', name: 'Marta Kowalska' });
		expect(hoisted.listAdminApiKeys).toHaveBeenCalledWith({ userId: 'u1', status: 'all', page: 1, pageSize: 8 });
		expect(target.querySelectorAll('[data-user-key]')).toHaveLength(2);
		expect(target.querySelectorAll('button[aria-label="Revoke key"]')).toHaveLength(1);
		expect(target.textContent).toContain('12 keys');
		expect(target.querySelector<HTMLAnchorElement>('a')!.getAttribute('href')).toBe('/admin?tab=settings&view=api_keys&owner=u1');
		await click(target.querySelector<HTMLElement>('button[aria-label="Revoke key"]')!);
		expect(hoisted.confirmDialog.mock.calls[0][0].title).toContain('for Marta Kowalska');
		expect(hoisted.revokeAdminApiKey).toHaveBeenCalledWith('k1');
	});

	it('lists people with their switches and pages through them', async () => {
		hoisted.getUsers.mockResolvedValue({
			success: true,
			data: Array.from({ length: 11 }, (_, i) => ({ id: `p${i}`, username: `Person ${i}`, email: `p${i}@example.com` }))
		});
		hoisted.getUserApiAccess.mockImplementation((id: string) =>
			Promise.resolve(access({ keys: id === 'p0' ? { active: 2, total: 3 } : id === 'p1' ? { active: 1, total: 1 } : { active: 0, total: 0 } }))
		);
		await render(PersonAccessSection);
		expect(target.querySelectorAll('[data-person]')).toHaveLength(8);
		expect(target.querySelector('[data-person-keys="p0"]')!.textContent).toBe('2 of 3 keys');
		expect(target.querySelector('[data-person-keys="p1"]')!.textContent).toBe('1 key');
		expect(target.querySelector('[data-person-keys="p2"]')!.textContent).toBe('0 keys');
		expect(hoisted.getUserApiAccess).toHaveBeenCalledTimes(8);
		await click(button(target, 'Page 2'));
		expect(target.querySelectorAll('[data-person]')).toHaveLength(3);
		const search = target.querySelector<HTMLInputElement>('input[type="search"]')!;
		search.value = 'person 10';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(target.querySelectorAll('[data-person]')).toHaveLength(1);
	});
});
