// @vitest-environment jsdom
//
// LLMConfigTab's configFormFrom() built every draft (detail-pane load, save
// reload, discard, duplicate) with `config?.temperature || 0.7`, so a stored
// temperature of 0 silently became 0.7 the moment the configuration was
// opened - then got sent back to the backend on the next unrelated save.
// Drives the real tab end to end through the actual draft/save handlers -
// not by calling configFormFrom() directly.
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { LLMConfig } from '$lib/types/llm';

const mockGetLLMConfigurations = vi.fn();
const mockGetPreChatActions = vi.fn();
const mockGetLLMAssignmentSummary = vi.fn();
const mockUpdateLLMConfiguration = vi.fn();
const mockCreateLLMConfiguration = vi.fn();
const mockDeleteLLMConfiguration = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getLLMConfigurations: (...args: unknown[]) => mockGetLLMConfigurations(...args),
		getPreChatActions: (...args: unknown[]) => mockGetPreChatActions(...args)
	}
}));

vi.mock('$lib/services/admin-api', () => ({
	getLLMAssignmentSummary: (...args: unknown[]) => mockGetLLMAssignmentSummary(...args),
	updateLLMConfiguration: (...args: unknown[]) => mockUpdateLLMConfiguration(...args),
	createLLMConfiguration: (...args: unknown[]) => mockCreateLLMConfiguration(...args),
	deleteLLMConfiguration: (...args: unknown[]) => mockDeleteLLMConfiguration(...args)
}));

const { default: LLMConfigTab } = await import('../../src/routes/admin/components/LLMConfigTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

function config(overrides: Partial<LLMConfig> = {}): LLMConfig {
	return {
		id: 'cfg-1',
		name: 'Zero-temp config',
		type: 'openai',
		enabled: true,
		base_url: '',
		api_key_set: false,
		model: 'gpt-4o',
		system_message: 'Be terse.',
		temperature: 0,
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

function selectFirstRow(target: HTMLElement) {
	const row = target.querySelector('[data-pane-row]') as HTMLElement | null;
	expect(row).toBeTruthy();
	row!.click();
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

function stubLoad(configs: LLMConfig[]) {
	mockGetLLMConfigurations.mockResolvedValue({ success: true, data: { configurations: configs } });
	mockGetPreChatActions.mockResolvedValue({ success: true, data: { actions: [] } });
	mockGetLLMAssignmentSummary.mockResolvedValue({ success: true, data: {} });
}

describe('LLMConfigTab — zero-valued temperature survives the real draft/save path', () => {
	it('loads temperature 0 into the detail pane, keeps it through Discard, and sends it back on Save', async () => {
		stubLoad([config()]);
		mockUpdateLLMConfiguration.mockResolvedValue({ success: true, data: config() });

		mounted = mount();
		await settle();
		selectFirstRow(mounted.target);
		await settle();

		// The detail pane's draft must show the stored 0, not a coerced 0.7.
		let tempInput = mounted.target.querySelector('#edit-config-temperature') as HTMLInputElement;
		expect(tempInput.value).toBe('0');

		// Dirty the pane, then discard - discardEditForm() re-runs configFormFrom()
		// against the same stored config and must still land on 0.
		const nameInput = mounted.target.querySelector('#edit-config-name') as HTMLInputElement;
		nameInput.value = 'Something else entirely';
		nameInput.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		clickButtonNamed(mounted.target, 'Discard');
		await settle();

		tempInput = mounted.target.querySelector('#edit-config-temperature') as HTMLInputElement;
		expect(tempInput.value).toBe('0');
		expect((mounted.target.querySelector('#edit-config-name') as HTMLInputElement).value).toBe('Zero-temp config');

		// Now make a real edit and save - the payload sent to the backend must
		// carry the 0 the pane displayed, not a re-coerced default.
		nameInput.value = 'Zero-temp config (renamed)';
		nameInput.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		clickButtonNamed(mounted.target, 'Save');
		await settle();

		expect(mockUpdateLLMConfiguration).toHaveBeenCalledTimes(1);
		const [, payload] = mockUpdateLLMConfiguration.mock.calls[0];
		expect(payload.temperature).toBe(0);
		expect(payload.name).toBe('Zero-temp config (renamed)');
	});

	it('carries temperature 0 into a duplicate sent to createLLMConfiguration', async () => {
		stubLoad([config()]);

		mounted = mount();
		await settle();
		selectFirstRow(mounted.target);
		await settle();

		const duplicateButton = mounted.target.querySelector(
			'button[aria-label="Duplicate configuration"]'
		) as HTMLButtonElement;
		expect(duplicateButton).toBeTruthy();
		duplicateButton.click();
		await settle();

		// BaseModal portals its content onto document.body, outside the tab's own target.
		const modalTempInput = document.querySelector('#create-config-temperature') as HTMLInputElement;
		expect(modalTempInput).toBeTruthy();
		expect(modalTempInput.value).toBe('0');

		clickButtonNamed(document.body, 'Create');
		await settle();

		expect(mockCreateLLMConfiguration).toHaveBeenCalledTimes(1);
		const [payload] = mockCreateLLMConfiguration.mock.calls[0];
		expect(payload.temperature).toBe(0);
		expect(payload.name).toBe('Zero-temp config (copy)');
	});

	it('defaults a missing temperature to 0.7 and leaves an explicit 0.3 untouched', async () => {
		stubLoad([
			config({ id: 'cfg-missing', name: 'Missing temp', temperature: undefined as unknown as number }),
			config({ id: 'cfg-point-three', name: 'Point-three temp', temperature: 0.3 })
		]);

		mounted = mount();
		await settle();

		const rows = mounted.target.querySelectorAll('[data-pane-row]');
		expect(rows).toHaveLength(2);

		(rows[0] as HTMLElement).click();
		await settle();
		expect((mounted.target.querySelector('#edit-config-temperature') as HTMLInputElement).value).toBe('0.7');

		(rows[1] as HTMLElement).click();
		await settle();
		expect((mounted.target.querySelector('#edit-config-temperature') as HTMLInputElement).value).toBe('0.3');
	});
});
