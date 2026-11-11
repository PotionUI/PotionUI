import { describe, it, expect, vi, beforeEach } from 'vitest';
import { settle } from './helpers/settle';

const hoisted = vi.hoisted(() => ({
	goto: vi.fn(),
	getSetupStatus: vi.fn(),
	getLoginProviders: vi.fn()
}));

vi.mock('$app/navigation', () => ({ goto: hoisted.goto }));
vi.mock('$lib/services/api/index', () => ({
	api: { getSetupStatus: hoisted.getSetupStatus, getLoginProviders: hoisted.getLoginProviders }
}));
vi.mock('$lib/stores/auth', () => ({
	authStore: {
		finishSignIn: vi.fn(),
		login: vi.fn(),
		subscribe: (fn: (v: unknown) => void) => {
			fn({ isAuthenticated: false, loading: false, error: null, user: null, token: null });
			return () => {};
		}
	}
}));

const page = (await import('$app/stores')).page as unknown as {
	set: (v: { url: URL }) => void;
};
const { default: LoginPage } = await import('../../src/routes/login/+page.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mountPage() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: LoginPage as never, target, props: {} });
	return () => {
		component.$destroy();
		target.remove();
	};
}

beforeEach(() => {
	sessionStorage.clear();
	hoisted.goto.mockReset();
	hoisted.getSetupStatus.mockReset();
	hoisted.getLoginProviders.mockReset().mockResolvedValue([]);
	page.set({ url: new URL('http://localhost/login') });
});

describe('login page on an instance without an owner', () => {
	it('goes to the claim screen', async () => {
		hoisted.getSetupStatus.mockResolvedValue({
			needs_owner: true,
			registration_open: true,
			claim_requires_token: false
		});
		const destroy = mountPage();
		await settle();
		expect(hoisted.goto).toHaveBeenCalledWith('/setup/claim', { replaceState: true });
		destroy();
	});

	it('stays on the login form once the instance has an owner', async () => {
		hoisted.getSetupStatus.mockResolvedValue({
			needs_owner: false,
			registration_open: false,
			claim_requires_token: false
		});
		const destroy = mountPage();
		await settle();
		expect(hoisted.goto).not.toHaveBeenCalled();
		destroy();
	});
});
