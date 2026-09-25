// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync, tick } from 'svelte';
import type { DiscoveredLLMModel } from '../../src/lib/types/llm';

vi.mock('$lib/services/api/index', () => ({
	api: {
		discoverLLMModels: vi.fn(),
		getLLMProviderTypes: vi.fn(),
		listNativeCheckpoints: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: LLMModelCombobox } = await import(
	'../../src/routes/admin/components/llm/LLMModelCombobox.svelte'
);
const { default: LLMConfigForm } = await import('../../src/routes/admin/components/LLMConfigForm.svelte');
const { createClassComponent } = await import('svelte/legacy');

const discover = vi.mocked(api.discoverLLMModels);
const providerTypes = vi.mocked(api.getLLMProviderTypes);

const MODELS: DiscoveredLLMModel[] = [
	{ id: 'qwen3:8b', size: 5_200_000_000, details: { parameter_size: '8.2B', quantization: 'Q4_K_M' } },
	{ id: 'llama3.2:latest', size: 2_000_000_000, details: { parameter_size: '3.2B' } },
	{ id: 'gemma3:12b', details: { family: 'gemma3' } }
];

async function settle() {
	await vi.runAllTimersAsync();
	await tick();
	flushSync();
}

function mountCombobox(props: Record<string, unknown> = {}) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: LLMModelCombobox as never,
		target,
		props: { id: 'm', value: '', type: 'ollama', baseUrl: 'http://ollama:11434', debounceMs: 10, ...props }
	});
	const input = () => target.querySelector<HTMLInputElement>('input[role="combobox"]')!;
	return {
		target,
		component,
		input,
		options: () => Array.from(document.querySelectorAll<HTMLElement>('[role="listbox"] [role="option"]')),
		key: (key: string) => {
			input().dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
			flushSync();
		},
		type: (text: string) => {
			input().value = text;
			input().dispatchEvent(new Event('input', { bubbles: true }));
			flushSync();
		}
	};
}

beforeEach(() => {
	vi.useFakeTimers();
	discover.mockReset();
	providerTypes.mockReset();
	discover.mockResolvedValue({ success: true, data: { supported: true, models: MODELS, error: null } });
});

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = '';
});

describe('LLMModelCombobox', () => {
	it('fetches with the form inputs and lists models with their meta', async () => {
		const view = mountCombobox({ apiKey: 'sk-typed', configId: 'cfg-1' });
		await settle();

		expect(discover).toHaveBeenCalledTimes(1);
		expect(discover).toHaveBeenCalledWith({
			type: 'ollama',
			base_url: 'http://ollama:11434',
			api_key: 'sk-typed',
			config_id: 'cfg-1'
		});

		view.input().dispatchEvent(new FocusEvent('focus'));
		flushSync();
		const rows = view.options();
		expect(rows.map((r) => r.querySelector('.font-mono')?.textContent)).toEqual(['qwen3:8b', 'llama3.2:latest', 'gemma3:12b']);
		expect(rows[0].textContent).toContain('8.2B · Q4_K_M · 4.8 GB');
	});

	it('filters case-insensitively over name and details and highlights the match', async () => {
		const view = mountCombobox();
		await settle();

		view.type('LLAMA');
		expect(view.options()).toHaveLength(1);
		expect(view.options()[0].querySelector('mark')?.textContent).toBe('llama');

		view.type('q4_k');
		expect(view.options().map((r) => r.id)).toEqual(['m-option-0']);
		expect(view.options()[0].textContent).toContain('qwen3:8b');
	});

	it('navigates with the arrow keys, picks with Enter and closes with Escape', async () => {
		const view = mountCombobox();
		await settle();

		view.input().dispatchEvent(new FocusEvent('focus'));
		flushSync();
		view.key('ArrowDown');
		view.key('ArrowDown');
		expect(view.options()[2].getAttribute('aria-selected')).toBe('true');
		view.key('ArrowUp');
		expect(view.input().getAttribute('aria-activedescendant')).toBe('m-option-1');
		view.key('Enter');
		expect(view.input().value).toBe('llama3.2:latest');
		expect(view.options()).toHaveLength(0);

		view.key('ArrowDown');
		expect(view.options().length).toBeGreaterThan(0);
		view.key('Escape');
		expect(view.options()).toHaveLength(0);
		expect(view.input().getAttribute('aria-expanded')).toBe('false');
	});

	it('offers the typed value as a custom model when nothing matches', async () => {
		const view = mountCombobox();
		await settle();

		view.type('mistral-nemo:12b');
		const custom = document.querySelector<HTMLElement>('[data-testid="llm-model-custom"]');
		expect(custom?.textContent).toContain('No models match');
		expect(custom?.textContent).toContain('mistral-nemo:12b');
		view.key('Enter');
		expect(view.input().value).toBe('mistral-nemo:12b');
		expect(document.querySelector('[data-testid="llm-model-custom"]')).toBeNull();
	});

	it('refetches from the refresh button', async () => {
		const view = mountCombobox();
		await settle();
		expect(discover).toHaveBeenCalledTimes(1);

		view.target.querySelector<HTMLButtonElement>('button[aria-label="Refresh models"]')!.click();
		await settle();
		expect(discover).toHaveBeenCalledTimes(2);
	});

	it('renders the discovery error under the field', async () => {
		discover.mockResolvedValue({
			success: true,
			data: {
				supported: true,
				models: [],
				error: "Couldn't reach Ollama at http://ollama:11434: connection refused."
			}
		});
		const view = mountCombobox();
		await settle();

		const error = view.target.querySelector('[data-testid="llm-model-error"]');
		expect(error?.textContent).toBe("Couldn't reach Ollama at http://ollama:11434: connection refused.");
	});
});

function draft(type: string) {
	return {
		name: 'x',
		type,
		model: '',
		api_key: '',
		base_url: 'http://host:11434',
		enabled: true,
		supports_vision: false,
		disable_system_prompt: false,
		memory_reflection: true,
		system_message: '',
		temperature: 0.7,
		max_tokens: 1000,
		timeout: 30,
		provider_options: {}
	};
}

function mountForm(type: string) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({
		component: LLMConfigForm as never,
		target,
		props: { draft: draft(type), mode: 'create', idPrefix: 'c' }
	});
	return target;
}

describe('LLMConfigForm model field', () => {
	beforeEach(() => {
		providerTypes.mockResolvedValue({
			success: true,
			data: {
				types: [
					{ type: 'ollama', supports_model_listing: true },
					{ type: 'openai', supports_model_listing: true },
					{ type: 'native', supports_model_listing: false }
				]
			}
		});
	});

	it('renders the combobox for a type that can list models', async () => {
		const target = mountForm('ollama');
		await settle();
		expect(target.querySelector('#c-model')?.getAttribute('role')).toBe('combobox');
		expect(target.querySelector('button[aria-label="Refresh models"]')).not.toBeNull();
	});

	it('keeps the plain text input for a type without listing', async () => {
		const target = mountForm('anthropic');
		await settle();
		const input = target.querySelector('#c-model');
		expect(input?.getAttribute('role')).toBeNull();
		expect(input?.getAttribute('type')).toBe('text');
		expect(target.querySelector('button[aria-label="Refresh models"]')).toBeNull();
		expect(discover).not.toHaveBeenCalled();
	});
});
