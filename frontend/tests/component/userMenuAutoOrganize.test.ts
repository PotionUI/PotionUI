// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { writable } from 'svelte/store';

const { goto, get } = vi.hoisted(() => ({ goto: vi.fn(), get: vi.fn() }));

vi.mock('$app/navigation', () => ({ goto }));
vi.mock('$lib/services/api/index', () => ({
	api: { getClient: () => ({ get, put: vi.fn() }) }
}));
vi.mock('$lib/stores/auth', () => ({
	authStore: Object.assign(
		writable({
			user: { id: 'u1', username: 'ada', account_type: 'USER', avatar_url: null, content_restricted: false }
		}),
		{ logout: vi.fn() }
	)
}));

const { autoOrganizeCounts } = await import('$lib/stores/autoOrganizeCounts');
const { LAST_SUBJECT_KEY } = await import('$lib/organize/lastSubject');
const { default: UserMenu } = await import('$lib/components/UserMenu.svelte');
const { createClassComponent } = await import('svelte/legacy');

const summary = {
	data: {
		subjects: {
			generation: { active: 2, needs_attention: 1 },
			upload: { active: 1, needs_attention: 0 },
			model: { active: 0, needs_attention: 0 }
		}
	}
};

async function openMenu() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: UserMenu as never, target, props: {} });
	target.querySelector<HTMLButtonElement>('button[aria-label="Account menu"]')!.click();
	for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

beforeEach(() => {
	autoOrganizeCounts.reset();
	localStorage.clear();
	goto.mockClear();
	get.mockReset();
});

afterEach(() => localStorage.clear());

describe('UserMenu Auto-organize entry', () => {
	it('renders directly after Settings with the total count and attention dot', async () => {
		get.mockResolvedValue({ data: summary });
		const menu = await openMenu();
		const items = [...menu.target.querySelectorAll('[role="menuitem"]')].map((el) =>
			el.textContent?.trim()
		);
		const settings = items.findIndex((t) => t === 'Settings');
		expect(items[settings + 1]).toMatch(/^Auto-organize/);
		expect(menu.target.querySelector('[data-testid="user-menu-auto-organize-count"]')?.textContent).toBe('3');
		expect(menu.target.querySelector('[data-testid="user-menu-auto-organize-attention"]')).not.toBeNull();
		menu.destroy();
	});

	it('omits the dot when nothing needs attention', async () => {
		get.mockResolvedValue({
			data: { subjects: { generation: { active: 1, needs_attention: 0 } } }
		});
		const menu = await openMenu();
		expect(menu.target.querySelector('[data-testid="user-menu-auto-organize-count"]')?.textContent).toBe('1');
		expect(menu.target.querySelector('[data-testid="user-menu-auto-organize-attention"]')).toBeNull();
		menu.destroy();
	});

	it('shows the entry without a count when the summary 404s', async () => {
		get.mockRejectedValue({ response: { status: 404 } });
		const menu = await openMenu();
		expect(menu.target.querySelector('[data-testid="user-menu-auto-organize"]')).not.toBeNull();
		expect(menu.target.querySelector('[data-testid="user-menu-auto-organize-count"]')).toBeNull();
		menu.destroy();
	});

	it('navigates to the last used subject and closes the menu', async () => {
		get.mockResolvedValue({ data: summary });
		localStorage.setItem(LAST_SUBJECT_KEY, 'models');
		const menu = await openMenu();
		menu.target.querySelector<HTMLButtonElement>('[data-testid="user-menu-auto-organize"]')!.click();
		expect(goto).toHaveBeenCalledWith('/auto-organize?subject=models');
		await new Promise((r) => setTimeout(r, 0));
		expect(menu.target.querySelector('[role="menu"]')).toBeNull();
		menu.destroy();
	});

	it('falls back to generations when nothing or junk is stored', async () => {
		get.mockResolvedValue({ data: summary });
		localStorage.setItem(LAST_SUBJECT_KEY, 'nonsense');
		const menu = await openMenu();
		menu.target.querySelector<HTMLButtonElement>('[data-testid="user-menu-auto-organize"]')!.click();
		expect(goto).toHaveBeenCalledWith('/auto-organize?subject=generations');
		menu.destroy();
	});
});
