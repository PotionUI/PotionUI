// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';
import type { User } from '$lib/stores/auth';

type PageStore = Writable<{ url: URL }>;

vi.mock('$lib/services/admin-api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/admin-api')>('$lib/services/admin-api');
	return {
		...actual,
		getUsers: vi.fn(),
		getUserGroups: vi.fn(),
		getGroupMembers: vi.fn(),
		getMcpUserSetting: vi.fn(),
		deleteUser: vi.fn(),
		deleteUserGroup: vi.fn(),
		addUsersToGroup: vi.fn(),
		assignPresetToUsers: vi.fn()
	};
});
vi.mock('$lib/services/api/index', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api/index')>('$lib/services/api/index');
	return {
		...actual,
		api: {
			...actual.api,
			getLLMConfigurations: vi.fn(),
			listPresets: vi.fn()
		}
	};
});
vi.mock('$app/navigation', async () => {
	const { page } = await import('$app/stores');
	const store = page as unknown as PageStore;
	return {
		goto: async (href: string) => {
			store.update((current) => ({ ...current, url: new URL(href, 'http://localhost') }));
		},
		invalidate: async () => {},
		invalidateAll: async () => {},
		preloadData: async () => {},
		preloadCode: async () => {},
		afterNavigate: () => {},
		beforeNavigate: () => {},
		pushState: () => {},
		replaceState: () => {}
	};
});

const adminApi = await import('$lib/services/admin-api');
const api = await import('$lib/services/api/index');
const page = (await import('$app/stores')).page as unknown as PageStore;
const { default: UsersGroupsTab } = await import('../../src/routes/admin/components/UsersGroupsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

function user(overrides: Partial<User> = {}): User {
	return {
		id: 'user-a',
		username: 'someuser',
		email: 'someuser@potionui.local',
		account_type: 'USER',
		created_at: '2026-01-01T00:00:00Z',
		last_login: null,
		avatar_url: null,
		has_local_password: true,
		...overrides
	};
}

function group(overrides: Partial<any> = {}) {
	return {
		id: 'group-a',
		name: 'Editors',
		description: 'People who edit things',
		is_system: false,
		member_count: 0,
		preset_count: 0,
		llm_count: 0,
		model_count: 0,
		...overrides
	};
}

function setUrl(search: string) {
	page.update((current) => ({ ...current, url: new URL(`http://localhost/admin${search}`) }));
}

function mount(props: Record<string, unknown> = { currentUser: { id: 'admin-1' } }) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: UsersGroupsTab as never, target, props });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	setUrl('');
});

describe('UsersGroupsTab', () => {
	it('lists users in the table and opens a user detail via the id param', async () => {
		const userA = user({ id: 'user-a', username: 'someuser' });
		const userB = user({ id: 'user-b', username: 'otheruser', account_type: 'ADMIN' });
		vi.mocked(adminApi.getUsers).mockResolvedValue({ success: true, data: [userA, userB] });
		vi.mocked(adminApi.getUserGroups).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.api.getLLMConfigurations).mockResolvedValue({ success: true, data: { configurations: [] } });
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [] });
		vi.mocked(adminApi.getMcpUserSetting).mockResolvedValue({ success: true, data: { user_id: 'user-a', enabled: true } });

		setUrl('?tab=users');
		mounted = mount();
		await settle();

		expect(mounted.target.textContent).toContain('someuser');
		expect(mounted.target.textContent).toContain('otheruser');
		expect(mounted.target.textContent).toContain('someuser@potionui.local');

		setUrl('?tab=users&id=user-a');
		await settle();

		expect(mounted.target.querySelector('h2')?.textContent).toBe('someuser');
		expect(mounted.target.textContent).toContain('someuser@potionui.local');
	});

	it('switches to the Groups directory section and lists groups', async () => {
		vi.mocked(adminApi.getUsers).mockResolvedValue({ success: true, data: [user()] });
		vi.mocked(adminApi.getUserGroups).mockResolvedValue({ success: true, data: [group({ id: 'group-a', name: 'Editors' })] });
		vi.mocked(adminApi.getGroupMembers).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.api.getLLMConfigurations).mockResolvedValue({ success: true, data: { configurations: [] } });
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [] });

		setUrl('?tab=users');
		mounted = mount();
		await settle();

		setUrl('?tab=users&view=groups');
		await settle();

		expect(mounted.target.textContent).toContain('Editors');
	});

	it('shows the bulk action bar once a user row is selected', async () => {
		vi.mocked(adminApi.getUsers).mockResolvedValue({ success: true, data: [user({ id: 'user-a', username: 'someuser' })] });
		vi.mocked(adminApi.getUserGroups).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.api.getLLMConfigurations).mockResolvedValue({ success: true, data: { configurations: [] } });
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [] });

		setUrl('?tab=users');
		mounted = mount();
		await settle();

		const checkbox = mounted.target.querySelector('[role="checkbox"][aria-label="Select row"]') as HTMLButtonElement;
		expect(checkbox).toBeTruthy();
		checkbox.click();
		flushSync();

		expect(mounted.target.textContent).toContain('1 selected');
		expect(mounted.target.textContent).toContain('Add to group');
		expect(mounted.target.textContent).toContain('Assign preset');
	});

	it('opens a group detail via the id param with its own header and tabs', async () => {
		vi.mocked(adminApi.getUsers).mockResolvedValue({ success: true, data: [] });
		vi.mocked(adminApi.getUserGroups).mockResolvedValue({
			success: true,
			data: [group({ id: 'group-a', name: 'Editors', member_count: 3 })]
		});
		vi.mocked(adminApi.getGroupMembers).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.api.getLLMConfigurations).mockResolvedValue({ success: true, data: { configurations: [] } });
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [] });

		setUrl('?tab=users&view=groups&id=group-a');
		mounted = mount();
		await settle();

		expect(mounted.target.querySelector('h2')?.textContent).toBe('Editors');
		expect(mounted.target.textContent).toContain('3 members');
		expect(mounted.target.querySelector('nav[aria-label="Group details"]')).toBeTruthy();
	});

	it('adds the selected users to a group through the bulk picker modal', async () => {
		vi.mocked(adminApi.getUsers).mockResolvedValue({ success: true, data: [user({ id: 'user-a', username: 'someuser' })] });
		vi.mocked(adminApi.getUserGroups).mockResolvedValue({ success: true, data: [group({ id: 'group-a', name: 'Editors' })] });
		vi.mocked(adminApi.getGroupMembers).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.api.getLLMConfigurations).mockResolvedValue({ success: true, data: { configurations: [] } });
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [] });
		vi.mocked(adminApi.addUsersToGroup).mockResolvedValue({ success: true });

		setUrl('?tab=users');
		mounted = mount();
		await settle();

		const checkbox = mounted.target.querySelector('[role="checkbox"][aria-label="Select row"]') as HTMLButtonElement;
		checkbox.click();
		flushSync();

		const addToGroupButton = Array.from(mounted.target.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Add to group')!;
		addToGroupButton.click();
		flushSync();
		await settle();

		const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
		expect(dialog).toBeTruthy();

		const groupOption = Array.from(dialog.querySelectorAll('[role="option"]')).find((el) => el.textContent?.includes('Editors'))!;
		expect(groupOption).toBeTruthy();
		(groupOption as HTMLElement).click();
		flushSync();

		const confirmButton = Array.from(dialog.querySelectorAll('button')).find((b) => b.textContent?.includes('Add to group'))!;
		expect(confirmButton).toBeTruthy();
		expect(confirmButton.disabled).toBe(false);
		confirmButton.click();
		await settle();

		expect(adminApi.addUsersToGroup).toHaveBeenCalledWith('group-a', ['user-a']);
	});
});
