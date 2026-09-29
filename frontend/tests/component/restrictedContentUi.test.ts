// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { writable } from 'svelte/store';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/services/api/index', () => ({
	api: { getClient: () => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }), put: vi.fn() }) }
}));
vi.mock('$lib/stores/auth', () => ({
	authStore: Object.assign(
		writable({
			user: { id: 'u1', username: 'kid', account_type: 'USER', avatar_url: null, content_restricted: true }
		}),
		{ logout: vi.fn() }
	)
}));

const { nsfwFilterStore } = await import('$lib/stores/nsfwFilter');
const { default: UserMenu } = await import('$lib/components/UserMenu.svelte');
const { createClassComponent } = await import('svelte/legacy');

async function openMenu() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: UserMenu as never, target, props: {} });
	target.querySelector<HTMLButtonElement>('button[aria-label="Account menu"]')!.click();
	for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

afterEach(() => nsfwFilterStore.reset());

describe('UserMenu sensitive content control', () => {
	it('is shown for an unrestricted account', async () => {
		nsfwFilterStore.setRestricted(false);
		const menu = await openMenu();
		expect(menu.target.querySelector('[aria-label="Sensitive content"]')).not.toBeNull();
		menu.destroy();
	});

	it('is hidden for a restricted account', async () => {
		nsfwFilterStore.setRestricted(true);
		const menu = await openMenu();
		expect(menu.target.querySelector('[role="menu"]')).not.toBeNull();
		expect(menu.target.querySelector('[aria-label="Sensitive content"]')).toBeNull();
		expect(menu.target.textContent).not.toContain('Sensitive content');
		menu.destroy();
	});
});
