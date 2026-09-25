// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { LLMConfig } from '$lib/types/llm';

const mockGetLLMConfigurations = vi.fn();
const mockGetPreChatActions = vi.fn();
const mockGetLLMAssignmentSummary = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getLLMConfigurations: (...args: unknown[]) => mockGetLLMConfigurations(...args),
		getPreChatActions: (...args: unknown[]) => mockGetPreChatActions(...args)
	}
}));

vi.mock('$lib/services/admin-api', () => ({
	getLLMAssignmentSummary: (...args: unknown[]) => mockGetLLMAssignmentSummary(...args)
}));

const { default: LLMConfigTab } = await import('../../src/routes/admin/components/LLMConfigTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

function config(overrides: Partial<LLMConfig> = {}): LLMConfig {
	return {
		id: 'cfg-1',
		name: 'Config one',
		type: 'openai',
		enabled: true,
		base_url: '',
		api_key_set: false,
		model: 'gpt-4o',
		system_message: 'Be terse.',
		temperature: 0.7,
		max_tokens: 1000,
		timeout: 30,
		supports_vision: false,
		memory_reflection: true,
		provider_options: {},
		...overrides
	};
}

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: LLMConfigTab as never, target, props: {} });
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

function clickButtonNamed(target: HTMLElement, text: string) {
	const button = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.trim() === text) as
		| HTMLButtonElement
		| undefined;
	expect(button).toBeTruthy();
	button!.click();
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
});

describe('LLMConfigTab — failed load shows an error state, not the empty state', () => {
	it('shows an error with Retry when the initial load fails, and the list once Retry succeeds', async () => {
		mockGetLLMConfigurations.mockResolvedValueOnce({ success: false, message: 'Backend unreachable' });
		mockGetPreChatActions.mockResolvedValue({ success: true, data: { actions: [] } });
		mockGetLLMAssignmentSummary.mockResolvedValue({ success: true, data: {} });

		mounted = mount();
		await settle();

		expect(mounted.target.textContent).toContain('Backend unreachable');
		expect(mounted.target.textContent).not.toContain('No LLM configurations yet');
		expect(mounted.target.querySelector('.dt-scroll')).toBeNull();

		mockGetLLMConfigurations.mockResolvedValueOnce({ success: true, data: { configurations: [config()] } });
		clickButtonNamed(mounted.target, 'Retry');
		await settle();

		expect(mounted.target.textContent).not.toContain('Backend unreachable');
		expect(mounted.target.textContent).toContain('Config one');
		expect(mockGetLLMConfigurations).toHaveBeenCalledTimes(2);
	});

	it('shows an error with Retry when the request throws', async () => {
		mockGetLLMConfigurations.mockRejectedValueOnce(new Error('Network down'));
		mockGetPreChatActions.mockResolvedValue({ success: true, data: { actions: [] } });
		mockGetLLMAssignmentSummary.mockResolvedValue({ success: true, data: {} });

		mounted = mount();
		await settle();

		expect(mounted.target.textContent).toContain('Network down');
		expect(mounted.target.textContent).not.toContain('No LLM configurations yet');
	});

	it('renders the real empty state on a successful empty response', async () => {
		mockGetLLMConfigurations.mockResolvedValueOnce({ success: true, data: { configurations: [] } });
		mockGetPreChatActions.mockResolvedValue({ success: true, data: { actions: [] } });
		mockGetLLMAssignmentSummary.mockResolvedValue({ success: true, data: {} });

		mounted = mount();
		await settle();

		expect(mounted.target.textContent).toContain('No LLM configurations yet');
	});
});
