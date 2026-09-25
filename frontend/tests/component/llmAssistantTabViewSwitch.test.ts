// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Writable } from 'svelte/store';

const mockGetLLMConfigurations = vi.fn();
const mockGetPreChatActions = vi.fn();
const mockGetLLMAssignmentSummary = vi.fn();
const mockGetAdminChatSessions = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getLLMConfigurations: (...args: unknown[]) => mockGetLLMConfigurations(...args),
		getPreChatActions: (...args: unknown[]) => mockGetPreChatActions(...args)
	}
}));

vi.mock('$lib/services/admin-api', () => ({
	getLLMAssignmentSummary: (...args: unknown[]) => mockGetLLMAssignmentSummary(...args),
	getAdminChatSessions: (...args: unknown[]) => mockGetAdminChatSessions(...args)
}));

const { page } = await import('$app/stores');
const { default: LLMAssistantTab } = await import('../../src/routes/admin/components/LLMAssistantTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

type PageStub = {
	url: URL;
	params: Record<string, never>;
	route: { id: null };
	status: number;
	error: null;
	data: Record<string, never>;
	form: undefined;
};

const pageStore = page as unknown as Writable<PageStub>;

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: LLMAssistantTab as never, target, props: {} });
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

function setUrl(search: string) {
	pageStore.set({
		url: new URL(`http://localhost/admin${search}`),
		params: {},
		route: { id: null },
		status: 200,
		error: null,
		data: {},
		form: undefined
	});
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	setUrl('?tab=llm');
});

describe('LLMAssistantTab — sidebar section switch', () => {
	it('defaults to Configurations: the create action shows and Sessions rows are hidden', async () => {
		mockGetLLMConfigurations.mockResolvedValue({ success: true, data: { configurations: [] } });
		mockGetPreChatActions.mockResolvedValue({ success: true, data: { actions: [] } });
		mockGetLLMAssignmentSummary.mockResolvedValue({ success: true, data: {} });
		mockGetAdminChatSessions.mockResolvedValue({
			success: true,
			data: { sessions: [], total: 0, limit: 20, offset: 0, tracing_enabled: true }
		});

		setUrl('?tab=llm');
		mounted = mount();
		await settle();

		const header = mounted.target.querySelector('header') as HTMLElement;
		expect(header).toBeTruthy();
		expect(Array.from(header.querySelectorAll('button')).some((b) => b.textContent?.trim() === 'Add configuration')).toBe(true);

		const hiddenSessionsWrapper = Array.from(mounted.target.querySelectorAll('.hidden')).find((el) =>
			el.textContent?.includes('No chat sessions yet')
		);
		expect(hiddenSessionsWrapper).toBeTruthy();
	});

	it('switching to Sessions hides the create-configuration action and shows the sessions empty state', async () => {
		mockGetLLMConfigurations.mockResolvedValue({ success: true, data: { configurations: [] } });
		mockGetPreChatActions.mockResolvedValue({ success: true, data: { actions: [] } });
		mockGetLLMAssignmentSummary.mockResolvedValue({ success: true, data: {} });
		mockGetAdminChatSessions.mockResolvedValue({
			success: true,
			data: { sessions: [], total: 0, limit: 20, offset: 0, tracing_enabled: true }
		});

		setUrl('?tab=llm');
		mounted = mount();
		await settle();

		setUrl('?tab=llm&view=sessions');
		await settle();

		const header = mounted.target.querySelector('header') as HTMLElement;
		expect(header).toBeTruthy();
		expect(Array.from(header.querySelectorAll('button')).some((b) => b.textContent?.trim() === 'Add configuration')).toBe(false);
		expect(mounted.target.textContent).toContain('No chat sessions yet');
	});
});
