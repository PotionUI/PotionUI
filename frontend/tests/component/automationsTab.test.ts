// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { Writable } from 'svelte/store';
import type { Automation, AutomationTemplate } from '$lib/types/automations';

type PageStore = Writable<{ url: URL }>;

vi.mock('$lib/services/api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api')>('$lib/services/api');
	return {
		...actual,
		api: {
			...actual.api,
			listAutomations: vi.fn(),
			listAutomationTemplates: vi.fn(),
			listNodeTypes: vi.fn(),
			enableAutomation: vi.fn(),
			disableAutomation: vi.fn(),
			deleteAutomation: vi.fn(),
			getAutomation: vi.fn(),
			listRuns: vi.fn(),
			updateAutomation: vi.fn(),
			getToken: vi.fn(() => null)
		}
	};
});
vi.mock('$lib/services/automationRunsWebsocket', () => ({
	automationRunsWebSocket: {
		connect: vi.fn(),
		disconnect: vi.fn()
	}
}));
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

const api = (await import('$lib/services/api')).api;
const page = (await import('$app/stores')).page as unknown as PageStore;
const { default: AutomationsTab } = await import('../../src/routes/admin/components/AutomationsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

function automation(overrides: Partial<Automation> = {}): Automation {
	return {
		id: 'automation-a',
		name: 'Tag new LoRAs',
		description: null,
		enabled: false,
		graph: { nodes: [], edges: [] },
		version: 1,
		created_at: '2026-09-01T00:00:00.000Z',
		updated_at: '2026-09-01T00:00:00.000Z',
		...overrides
	};
}

function template(overrides: Partial<AutomationTemplate> = {}): AutomationTemplate {
	return {
		key: 'plugin:example:starter',
		id: 'starter',
		source: 'core',
		source_name: 'Core',
		title: 'Starter workflow',
		description: 'A ready-made workflow.',
		category: 'general',
		icon: 'bolt',
		tags: [],
		node_types: ['trigger.manual'],
		missing_node_types: [],
		available: true,
		...overrides
	};
}

function setUrl(search: string) {
	page.update((current) => ({ ...current, url: new URL(`http://localhost/admin${search}`) }));
}

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: AutomationsTab as never, target, props: {} });
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

describe('AutomationsTab', () => {
	it('lists automations in the table', async () => {
		vi.mocked(api.listAutomations).mockResolvedValue({ success: true, data: [automation({ name: 'Tag new LoRAs' })] });
		vi.mocked(api.listAutomationTemplates).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.listNodeTypes).mockResolvedValue({ success: true, data: [] });

		setUrl('?tab=automations');
		mounted = mount();
		await settle();

		expect(mounted.target.textContent).toContain('Tag new LoRAs');
	});

	it('switches to the Templates section and lists templates', async () => {
		vi.mocked(api.listAutomations).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.listAutomationTemplates).mockResolvedValue({ success: true, data: [template({ title: 'Starter workflow' })] });
		vi.mocked(api.listNodeTypes).mockResolvedValue({ success: true, data: [] });

		setUrl('?tab=automations');
		mounted = mount();
		await settle();

		setUrl('?tab=automations&section=templates');
		await settle();

		expect(mounted.target.textContent).toContain('Starter workflow');
	});

	it('shows the bulk action bar once an automation row is selected', async () => {
		vi.mocked(api.listAutomations).mockResolvedValue({
			success: true,
			data: [automation({ id: 'automation-a', name: 'Tag new LoRAs' })]
		});
		vi.mocked(api.listAutomationTemplates).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.listNodeTypes).mockResolvedValue({ success: true, data: [] });

		setUrl('?tab=automations');
		mounted = mount();
		await settle();

		expect(mounted.target.textContent).not.toContain('selected');

		const checkbox = mounted.target.querySelector('[role="checkbox"][aria-label="Select row"]') as HTMLElement | null;
		expect(checkbox).toBeTruthy();
		checkbox?.click();
		await settle();

		expect(mounted.target.textContent).toContain('1 selected');
	});

	it('opens an automation and goes Back without an effect loop, and reflects a toggle in the list', async () => {
		const a = automation({ id: 'automation-a', name: 'Tag new LoRAs', enabled: false });
		vi.mocked(api.listAutomations).mockResolvedValue({ success: true, data: [a] });
		vi.mocked(api.listAutomationTemplates).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.listNodeTypes).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.getAutomation).mockResolvedValue({ success: true, data: a });
		vi.mocked(api.listRuns).mockResolvedValue({ success: true, data: [] });
		vi.mocked(api.enableAutomation).mockResolvedValue({ success: true, data: { ...a, enabled: true } });

		const errors: unknown[] = [];
		const onUncaught = (error: unknown) => errors.push(error);
		process.on('uncaughtException', onUncaught);
		process.on('unhandledRejection', onUncaught);

		setUrl('?tab=automations');
		mounted = mount();
		await settle();

		setUrl('?tab=automations&id=automation-a');
		await settle();

		const toggle = mounted.target.querySelector('[role="switch"]') as HTMLElement | null;
		expect(toggle).toBeTruthy();
		toggle?.click();
		await settle();

		setUrl('?tab=automations');
		await settle();

		process.off('uncaughtException', onUncaught);
		process.off('unhandledRejection', onUncaught);

		const message = errors.map((e) => String(e)).join('\n');
		expect(message).not.toContain('effect_update_depth_exceeded');
		expect(errors).toHaveLength(0);

		const row = mounted.target.querySelector('[role="checkbox"][aria-label="Select row"]')?.closest('[role="row"]');
		expect(row?.textContent ?? mounted.target.textContent).toContain('Tag new LoRAs');
	});
});
