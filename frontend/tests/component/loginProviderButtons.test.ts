import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getSetupStatus: vi.fn(),
		getLoginProviders: vi.fn()
	}
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: {
		subscribe: (fn: (v: unknown) => void) => {
			fn({ isAuthenticated: false, loading: false, error: null, user: null, token: null });
			return () => {};
		}
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: LoginPage } = await import('../../src/routes/login/+page.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mountPage() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: LoginPage as never,
		target,
		props: {}
	});
	return {
		target,
		component,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

function providerButtons(target: HTMLElement) {
	return Array.from(target.querySelectorAll('button')).filter((btn) =>
		btn.textContent?.includes('Continue with')
	);
}

function dividerRendered(target: HTMLElement) {
	return Array.from(target.querySelectorAll('span')).some((span) => span.textContent?.trim() === 'or');
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountPage> | undefined;
let originalLocationDescriptor: PropertyDescriptor | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	if (originalLocationDescriptor) {
		Object.defineProperty(window, 'location', originalLocationDescriptor);
		originalLocationDescriptor = undefined;
	}
	vi.clearAllMocks();
});

describe('login page provider buttons', () => {
	it('renders no provider buttons and no divider when the provider list is empty', async () => {
		vi.mocked(api.getSetupStatus).mockResolvedValue(null as never);
		vi.mocked(api.getLoginProviders).mockResolvedValue([] as never);

		mounted = mountPage();
		await settle();

		expect(providerButtons(mounted.target)).toHaveLength(0);
		expect(dividerRendered(mounted.target)).toBe(false);
	});

	it('renders one button per provider, labelled and ordered as returned', async () => {
		vi.mocked(api.getSetupStatus).mockResolvedValue(null as never);
		vi.mocked(api.getLoginProviders).mockResolvedValue([
			{ id: 'acme-sso', label: 'Acme SSO', start_path: '/api/plugins/acme-sso/start' },
			{ id: 'globex-oidc', label: 'Globex OIDC', start_path: '/api/plugins/globex-oidc/start' }
		] as never);

		mounted = mountPage();
		await settle();

		const buttons = providerButtons(mounted.target);
		expect(buttons).toHaveLength(2);
		expect(buttons[0].textContent?.trim()).toBe('Continue with Acme SSO');
		expect(buttons[1].textContent?.trim()).toBe('Continue with Globex OIDC');
		expect(dividerRendered(mounted.target)).toBe(true);
	});

	it("sends the browser to the clicked provider's start_path", async () => {
		vi.mocked(api.getSetupStatus).mockResolvedValue(null as never);
		vi.mocked(api.getLoginProviders).mockResolvedValue([
			{ id: 'acme-sso', label: 'Acme SSO', start_path: '/api/plugins/acme-sso/start' }
		] as never);

		originalLocationDescriptor = Object.getOwnPropertyDescriptor(window, 'location');
		const assignedHrefs: string[] = [];
		Object.defineProperty(window, 'location', {
			configurable: true,
			value: {
				get href() {
					return 'http://localhost/login';
				},
				set href(value: string) {
					assignedHrefs.push(value);
				}
			}
		});

		mounted = mountPage();
		await settle();

		const [button] = providerButtons(mounted.target);
		button.click();
		await settle();

		expect(assignedHrefs).toEqual(['/api/plugins/acme-sso/start']);
	});
});
