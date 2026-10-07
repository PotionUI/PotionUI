// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get, writable } from 'svelte/store';

const hoisted = vi.hoisted(() => ({
	goto: vi.fn(),
	setAuthHeader: vi.fn(),
	getCurrentUser: vi.fn(),
	clearAuth: vi.fn(),
	authActions: {
		logout: vi.fn(),
		logoutAccount: vi.fn(),
		logoutAll: vi.fn(),
		finishSignIn: vi.fn()
	}
}));

vi.mock('$app/navigation', () => ({ goto: hoisted.goto }));
vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }), put: vi.fn() }),
		setAuthHeader: hoisted.setAuthHeader,
		clearAuth: hoisted.clearAuth,
		getCurrentUser: hoisted.getCurrentUser,
		getToken: () => null,
		getSetupStatus: vi.fn().mockResolvedValue(null),
		getLoginProviders: vi.fn().mockResolvedValue([])
	}
}));

const authState = writable<Record<string, unknown>>({
	isAuthenticated: true,
	loading: false,
	error: null,
	token: 't',
	user: { id: 'alice', username: 'alice', account_type: 'ADMIN', avatar_url: null, content_restricted: false }
});

vi.mock('$lib/stores/auth', () => ({
	authStore: Object.assign({ subscribe: authState.subscribe }, hoisted.authActions)
}));

const accounts = await import('$lib/stores/accounts');
const { nsfwFilterStore } = await import('$lib/stores/nsfwFilter');
const { limits, storageUsed, resetLimitsState } = await import('$lib/plans/store');
const { ACCOUNTS_STORAGE_KEY } = await import('$lib/stores/accountRegistry');
const { activeConfirm, settleConfirm, cancelAllConfirms } = await import('$lib/stores/confirm');
const { page } = await import('../../tests/component/stubs/appStores');
const { default: UserMenu } = await import('$lib/components/UserMenu.svelte');
const { default: AccountsSheet } = await import('$lib/components/accounts/AccountsSheet.svelte');
const { default: LoginPage } = await import('../../src/routes/login/+page.svelte');
const { createClassComponent } = await import('svelte/legacy');

const NOW = Date.now();

interface Seed {
	id: string;
	expired?: boolean;
	signedInAt?: number;
}

function seed(activeId: string | null, rows: Seed[]) {
	localStorage.setItem(
		ACCOUNTS_STORAGE_KEY,
		JSON.stringify({
			v: 1,
			activeId,
			accounts: rows.map((r) => ({
				userId: r.id,
				username: r.id,
				role: r.id === 'alice' ? 'ADMIN' : 'USER',
				avatar: null,
				token: `tok-${r.id}`,
				signedInAt: r.signedInAt ?? NOW - 2 * 86_400_000,
				expired: r.expired ?? false,
				expiredAt: r.expired ? NOW - 1000 : undefined
			}))
		})
	);
	accounts.resetAccountsForTests();
}

const tick = async (n = 6) => {
	for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0));
};

function mount(component: unknown, props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: component as never, target, props });
	return {
		target,
		destroy: () => {
			instance.$destroy();
			target.remove();
		}
	};
}

async function openMenu() {
	const view = mount(UserMenu);
	view.target.querySelector<HTMLButtonElement>('button[aria-label="Account menu"]')!.click();
	await tick();
	return view;
}

function rows(target: HTMLElement) {
	return Array.from(target.querySelectorAll<HTMLElement>('[data-testid="account-row"]'));
}

function buttonByText(target: HTMLElement, text: string) {
	return Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
		b.textContent?.trim().includes(text)
	);
}

const location = { reload: vi.fn(), assign: vi.fn(), pathname: '/generate' };

beforeEach(() => {
	localStorage.clear();
	accounts.resetAccountsForTests();
	hoisted.goto.mockClear();
	hoisted.setAuthHeader.mockClear();
	hoisted.getCurrentUser.mockReset();
	hoisted.getCurrentUser.mockResolvedValue({ success: true, data: { id: 'bob', username: 'bob', account_type: 'USER', avatar_url: null } });
	for (const fn of Object.values(hoisted.authActions)) fn.mockClear();
	location.reload.mockClear();
	location.assign.mockClear();
	vi.stubGlobal('location', location);
	authState.set({
		isAuthenticated: true,
		loading: false,
		error: null,
		token: 't',
		user: { id: 'alice', username: 'alice', account_type: 'ADMIN', avatar_url: null, content_restricted: false }
	});
	page.update((p) => ({ ...p, url: new URL('http://localhost/generate') }));
});

afterEach(() => {
	cancelAllConfirms();
	nsfwFilterStore.reset();
	resetLimitsState();
	vi.unstubAllGlobals();
	document.body.innerHTML = '';
});

describe('UserMenu account switcher', () => {
	it('renders the active account header and one row per other account with count and signed in line', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }, { id: 'carol', signedInAt: NOW - 9 * 3_600_000 }]);
		const menu = await openMenu();

		expect(menu.target.querySelector('[data-testid="user-menu-active"]')?.textContent).toContain('Active');
		expect(menu.target.querySelector('[data-testid="user-menu-accounts"]')?.textContent).toContain('3 / 5');
		const list = rows(menu.target);
		expect(list.map((r) => r.dataset.userId)).toEqual(['bob', 'carol']);
		expect(list[0].textContent).toContain('signed in 2 d ago');
		expect(list[1].textContent).toContain('signed in 9 h ago');
		menu.destroy();
	});

	it('hides the other accounts block when there is only one account', async () => {
		seed('alice', [{ id: 'alice' }]);
		const menu = await openMenu();

		expect(menu.target.querySelector('[data-testid="user-menu-accounts"]')).toBeNull();
		expect(menu.target.querySelector('[data-testid="user-menu-logout-all"]')).toBeNull();
		expect(menu.target.querySelector('[data-testid="user-menu-add-account"]')).not.toBeNull();
		menu.destroy();
	});

	it('switching a row verifies the account token and reloads the page', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const menu = await openMenu();

		rows(menu.target)[0].querySelector<HTMLButtonElement>('button[role="menuitem"]')!.click();
		await tick();

		expect(hoisted.setAuthHeader).toHaveBeenCalledWith('tok-bob');
		expect(hoisted.getCurrentUser).toHaveBeenCalledTimes(1);
		expect(JSON.parse(localStorage.getItem(ACCOUNTS_STORAGE_KEY)!).activeId).toBe('bob');
		expect(location.reload).toHaveBeenCalledTimes(1);
		menu.destroy();
	});

	it('the per row x logs out just that account without asking for confirmation', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const menu = await openMenu();

		const x = rows(menu.target)[0].querySelector<HTMLButtonElement>('button[aria-label="Log out bob"]');
		expect(x).not.toBeNull();
		x!.click();

		expect(hoisted.authActions.logoutAccount).toHaveBeenCalledWith('bob');
		expect(get(activeConfirm)).toBeNull();
		menu.destroy();
	});

	it('Log out <name> logs out the active account without a confirmation', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const menu = await openMenu();

		const button = menu.target.querySelector<HTMLButtonElement>('[data-testid="user-menu-logout"]')!;
		expect(button.textContent).toContain('Log out alice');
		button.click();

		expect(hoisted.authActions.logout).toHaveBeenCalledTimes(1);
		expect(get(activeConfirm)).toBeNull();
		menu.destroy();
	});

	it('Log out of all accounts confirms first and only then logs everything out', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const menu = await openMenu();

		menu.target.querySelector<HTMLButtonElement>('[data-testid="user-menu-logout-all"]')!.click();
		await tick();

		const request = get(activeConfirm);
		expect(request?.title).toBe('Log out of all accounts?');
		expect(request?.message).toContain('alice, bob');
		expect(hoisted.authActions.logoutAll).not.toHaveBeenCalled();

		settleConfirm(request!.id, true);
		await tick();
		expect(hoisted.authActions.logoutAll).toHaveBeenCalledTimes(1);
		menu.destroy();
	});

	it('cancelling the Log out of all confirmation keeps every account', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const menu = await openMenu();

		menu.target.querySelector<HTMLButtonElement>('[data-testid="user-menu-logout-all"]')!.click();
		await tick();
		settleConfirm(get(activeConfirm)!.id, false);
		await tick();

		expect(hoisted.authActions.logoutAll).not.toHaveBeenCalled();
		menu.destroy();
	});

	it('shows an expired account dimmed with Sign in again, which opens the add flow prefilled', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob', expired: true }]);
		const menu = await openMenu();

		const row = rows(menu.target)[0];
		expect(row.textContent).toContain('Session expired');
		expect(row.textContent).toContain('Sign in again');
		expect(row.textContent).not.toContain('Switch');

		row.querySelector<HTMLButtonElement>('button[role="menuitem"]')!.click();
		expect(hoisted.goto).toHaveBeenCalledWith('/login?add=1&as=bob');
		expect(hoisted.setAuthHeader).not.toHaveBeenCalled();
		menu.destroy();
	});

	it('Add account opens the login page in add mode', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const menu = await openMenu();

		menu.target.querySelector<HTMLButtonElement>('[data-testid="user-menu-add-account"]')!.click();

		expect(hoisted.goto).toHaveBeenCalledWith('/login?add=1');
		menu.destroy();
	});

	it('disables Add account at the cap of five', async () => {
		seed('alice', ['alice', 'b', 'c', 'd', 'e'].map((id) => ({ id })));
		const menu = await openMenu();

		const add = menu.target.querySelector<HTMLButtonElement>('[data-testid="user-menu-add-account"]')!;
		expect(add.disabled).toBe(true);
		expect(add.title).toBe('Remove an account first');
		menu.destroy();
	});
});

const storageRow = {
	kind: 'storage_bytes',
	label: 'Storage',
	used: 3.2 * 2 ** 30,
	limit: 10 * 2 ** 30,
	format: 'bytes',
	resets_at: null,
	state: 'ok',
	percent: null,
	enforced: true
};

function headers(target: HTMLElement) {
	return Array.from(target.querySelectorAll('[data-section-header]')).map((h) => h.textContent?.replace(/\s+/g, ' ').trim());
}

describe('UserMenu section cards', () => {
	it('groups the menu into Accounts, Workspace, Content and Session cards', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const menu = await openMenu();

		expect(headers(menu.target)).toEqual(['Accounts 2 / 5', 'Workspace', 'Content', 'Session']);
		const identity = menu.target.querySelector('[data-testid="user-menu-identity"]')!;
		expect(identity.textContent).toContain('alice');
		expect(identity.textContent).toContain('Admin');
		expect(identity.textContent).not.toContain('@');
		menu.destroy();
	});

	it('drops the Content section for restricted users', async () => {
		seed('alice', [{ id: 'alice' }]);
		nsfwFilterStore.setRestricted(true);
		const menu = await openMenu();

		expect(headers(menu.target)).toEqual(['Accounts 1 / 5', 'Workspace', 'Session']);
		expect(menu.target.querySelector('[role="radiogroup"]')).toBeNull();
		menu.destroy();
	});

	it('puts Sensitive content on one row with the segmented control', async () => {
		seed('alice', [{ id: 'alice' }]);
		const menu = await openMenu();

		const row = menu.target.querySelector('[data-testid="user-menu-content-row"]')!;
		expect(row.textContent).toContain('Sensitive content');
		expect(Array.from(row.querySelectorAll('[role="radio"]')).map((r) => r.textContent?.trim())).toEqual([
			'Blur',
			'Show',
			'Hide'
		]);
		menu.destroy();
	});

	it('shows storage with no limit and no meter without a quota', async () => {
		seed('alice', [{ id: 'alice' }]);
		storageUsed.set(0);
		const menu = await openMenu();

		expect(menu.target.querySelector('[data-menu-storage]')?.textContent).toContain('0 B · no limit');
		expect(menu.target.querySelector('[role="progressbar"]')).toBeNull();
		menu.destroy();
	});

	it('shows a quota meter with the plan link, and the warning colour near the limit', async () => {
		seed('alice', [{ id: 'alice' }]);
		limits.set([{ ...storageRow, used: 9.4 * 2 ** 30, state: 'warn' }] as never);
		const menu = await openMenu();

		const usage = menu.target.querySelector('[data-menu-usage]')!;
		expect(usage.textContent).toContain('Storage');
		expect(usage.textContent).toContain('9.4 / 10 GB');
		expect(usage.textContent).toContain('Plan');
		expect(usage.querySelector('.bg-warning')).not.toBeNull();
		menu.destroy();
	});

	it('keeps the signal meter below the warning threshold', async () => {
		seed('alice', [{ id: 'alice' }]);
		limits.set([storageRow] as never);
		const menu = await openMenu();

		const usage = menu.target.querySelector('[data-menu-usage]')!;
		expect(usage.querySelector('.bg-signal')).not.toBeNull();
		expect(usage.querySelector('.bg-warning')).toBeNull();
		menu.destroy();
	});

	it('is 384 px wide and scrolls its body so Log out stays on screen', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const menu = await openMenu();

		const scroll = menu.target.querySelector<HTMLElement>('[data-testid="user-menu-scroll"]')!;
		expect(scroll.className).toContain('overflow-y-auto');
		expect(scroll.style.maxHeight).toContain('720px');
		expect(scroll.closest('[role="menu"]')!.className).toContain('w-96');
		expect(scroll.contains(menu.target.querySelector('[data-testid="user-menu-logout"]'))).toBe(true);
		menu.destroy();
	});

	it('says Limit reached on the disabled Add account at the cap', async () => {
		seed('alice', ['alice', 'b', 'c', 'd', 'e'].map((id) => ({ id })));
		const menu = await openMenu();

		expect(menu.target.querySelector('[data-testid="user-menu-add-account"]')?.textContent).toContain('Limit reached');
		menu.destroy();
	});
});

describe('AccountsSheet (mobile)', () => {
	it('lists every account with the active one first, the count and the log out entries', async () => {
		seed('alice', [{ id: 'bob' }, { id: 'alice' }, { id: 'carol', expired: true }]);
		const view = mount(AccountsSheet, { onClose: vi.fn() });
		await tick();

		const list = rows(document.body);
		expect(list.map((r) => r.dataset.userId)).toEqual(['bob', 'carol']);
		expect(document.querySelector('[data-testid="user-menu-active"]')?.textContent).toContain('Active');
		expect(list[1].textContent).toContain('Session expired');
		expect(document.querySelector('[data-testid="accounts-sheet-count"]')?.textContent).toBe('3 / 5');
		expect(document.querySelector('[data-testid="accounts-sheet-logout"]')?.textContent).toContain('Log out alice');
		expect(document.querySelector('[data-testid="accounts-sheet-logout-all"]')).not.toBeNull();
		view.destroy();
	});

	it('uses the same four section cards and stacks Sensitive content over a full width control', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const view = mount(AccountsSheet, { onClose: vi.fn() });
		await tick();

		expect(headers(document.body)).toEqual(['Accounts 2 / 5', 'Workspace', 'Content', 'Session']);
		const row = document.querySelector('[data-testid="user-menu-content-row"]')!;
		expect(row.className).toContain('flex-col');
		expect(row.querySelector('[role="radiogroup"]')!.className).toContain('w-full');
		expect(rows(document.body)[0].querySelector('button[role="menuitem"]')!.className).toContain('min-h-[52px]');
		view.destroy();
	});

	it('switching from the sheet closes it and runs the switch flow', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const onClose = vi.fn();
		const view = mount(AccountsSheet, { onClose });
		await tick();

		rows(document.body)[0].querySelector<HTMLButtonElement>('button[role="menuitem"]')!.click();
		await tick();

		expect(onClose).toHaveBeenCalled();
		expect(hoisted.setAuthHeader).toHaveBeenCalledWith('tok-bob');
		expect(location.reload).toHaveBeenCalledTimes(1);
		view.destroy();
	});

	it('the row x on the sheet logs out only that account', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		const view = mount(AccountsSheet, { onClose: vi.fn() });
		await tick();

		document.querySelector<HTMLButtonElement>('button[aria-label="Log out bob"]')!.click();

		expect(hoisted.authActions.logoutAccount).toHaveBeenCalledWith('bob');
		expect(get(activeConfirm)).toBeNull();
		view.destroy();
	});
});

describe('login page account flows', () => {
	function setUrl(search: string) {
		page.update((p) => ({ ...p, url: new URL(`http://localhost/login${search}`) }));
	}

	it('shows the Continue as list after the active account expires and never auto-switches', async () => {
		seed('alice', [{ id: 'alice', expired: true }, { id: 'bob' }, { id: 'carol' }]);
		authState.set({ isAuthenticated: false, loading: false, error: null, token: null, user: null });
		setUrl('?expired=1');
		const view = mount(LoginPage);
		await tick();

		const list = view.target.querySelectorAll('[data-testid="continue-as-row"]');
		expect(Array.from(list).map((r) => (r as HTMLElement).dataset.userId)).toEqual(['bob', 'carol']);
		expect(view.target.textContent).toContain("alice's session has expired. Pick another account, or sign in again below.");
		expect((view.target.querySelector('#username') as HTMLInputElement).value).toBe('alice');
		expect(hoisted.setAuthHeader).not.toHaveBeenCalled();
		expect(location.reload).not.toHaveBeenCalled();
		view.destroy();
	});

	it('does not list expired accounts under Continue as', async () => {
		seed(null, [{ id: 'bob', expired: true }, { id: 'carol' }]);
		authState.set({ isAuthenticated: false, loading: false, error: null, token: null, user: null });
		setUrl('');
		const view = mount(LoginPage);
		await tick();

		const list = view.target.querySelectorAll<HTMLElement>('[data-testid="continue-as-row"]');
		expect(Array.from(list).map((r) => r.dataset.userId)).toEqual(['carol']);
		view.destroy();
	});

	it('choosing an account runs the switch flow from the login page', async () => {
		seed(null, [{ id: 'bob' }]);
		authState.set({ isAuthenticated: false, loading: false, error: null, token: null, user: null });
		setUrl('');
		location.pathname = '/login';
		const view = mount(LoginPage);
		await tick();

		view.target.querySelector<HTMLButtonElement>('[data-testid="continue-as-row"]')!.click();
		await tick();

		expect(hoisted.setAuthHeader).toHaveBeenCalledWith('tok-bob');
		expect(location.assign).toHaveBeenCalledWith('/generate');
		view.destroy();
	});

	it('renders no Continue as list when there are no stored accounts', async () => {
		authState.set({ isAuthenticated: false, loading: false, error: null, token: null, user: null });
		setUrl('');
		const view = mount(LoginPage);
		await tick();

		expect(view.target.querySelector('[data-testid="continue-as"]')).toBeNull();
		view.destroy();
	});

	it('add mode keeps the user signed in, renames the button and offers a way back', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		setUrl('?add=1');
		const view = mount(LoginPage);
		await tick();

		expect(view.target.textContent).toContain('Add an account');
		expect(view.target.textContent).toContain('You stay signed in as alice');
		expect(buttonByText(view.target, 'Add account')).toBeDefined();
		expect(buttonByText(view.target, 'Sign In')).toBeUndefined();
		expect(view.target.querySelector('[data-testid="continue-as"]')).toBeNull();
		expect(view.target.textContent).toContain('Cancel, back to alice');
		expect(hoisted.goto).not.toHaveBeenCalledWith('/generate');
		view.destroy();
	});

	it('add mode prefills the username for Sign in again', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob', expired: true }]);
		setUrl('?add=1&as=bob');
		const view = mount(LoginPage);
		await tick();

		expect((view.target.querySelector('#username') as HTMLInputElement).value).toBe('bob');
		view.destroy();
	});

	it('shows the duplicate notice with Switch and Stay choices', async () => {
		seed('alice', [{ id: 'alice' }, { id: 'bob' }]);
		setUrl('?add=1&dup=bob');
		const view = mount(LoginPage);
		await tick();

		expect(view.target.textContent).toContain('bob is already signed in.');
		expect(view.target.textContent).toContain('Nothing was added');
		expect(buttonByText(view.target, 'Switch to bob')).toBeDefined();
		expect(buttonByText(view.target, 'Stay as alice')).toBeDefined();
		expect(buttonByText(view.target, 'Add account')).toBeUndefined();

		buttonByText(view.target, 'Switch to bob')!.click();
		await tick();
		expect(hoisted.setAuthHeader).toHaveBeenCalledWith('tok-bob');
		view.destroy();
	});
});
