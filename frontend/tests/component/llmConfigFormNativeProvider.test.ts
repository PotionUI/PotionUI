// @vitest-environment jsdom
//
// LLMConfigForm: the native provider's checkpoint picker (fed by
// GET /api/llm/native/checkpoints) and its "Native Options" section
// (thinking mode / quantization / capability badges) — mounted end to end so
// a change to the fetch, the select markup, or the provider_options
// round-trip actually breaks a test, not just a hand-read of the source.
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { LLMConfigFormData } from '../../src/routes/admin/components/LLMConfigForm.svelte';
import type { NativeCheckpoint } from '$lib/types/llm';

vi.mock('$lib/services/api/index', () => ({
	api: { listNativeCheckpoints: vi.fn() }
}));

const api = await import('$lib/services/api/index');
const { default: LLMConfigForm } = await import(
	'../../src/routes/admin/components/LLMConfigForm.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function makeDraft(overrides: Partial<LLMConfigFormData> = {}): LLMConfigFormData {
	return {
		name: 'Test config',
		type: 'openai',
		model: '',
		api_key: '',
		base_url: '',
		enabled: true,
		supports_vision: false,
		disable_system_prompt: false,
		memory_reflection: true,
		system_message: 'You are helpful.',
		temperature: 0.7,
		max_tokens: 1000,
		timeout: 30,
		provider_options: {},
		...overrides
	};
}

function checkpoint(overrides: Partial<NativeCheckpoint> = {}): NativeCheckpoint {
	return {
		name: 'qwen3-tiny',
		path: '/models/llm/qwen3-tiny',
		model_type: 'qwen3',
		supported: true,
		vision: false,
		reason: null,
		quant_modes: ['none', 'int8', 'nf4'],
		shared_te: false,
		...overrides
	};
}

function ok(data: NativeCheckpoint[]) {
	return { success: true, data };
}

function mount(draft: LLMConfigFormData) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: LLMConfigForm as never,
		target,
		props: { draft, mode: 'create', idPrefix: 'test' }
	});
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
});

describe('LLMConfigForm — native checkpoint picker', () => {
	it('shows an empty state when no checkpoints are found', async () => {
		vi.mocked(api.api.listNativeCheckpoints).mockResolvedValue(ok([]));

		const draft = makeDraft({ type: 'native' });
		mounted = mount(draft);
		await settle();

		expect(mounted.target.textContent).toContain('No checkpoints found under models/llm/');
	});

	it('shows an error state with a Retry action when the endpoint fails', async () => {
		vi.mocked(api.api.listNativeCheckpoints).mockResolvedValue({
			success: false,
			message: 'could not reach the checkpoint scanner'
		});

		const draft = makeDraft({ type: 'native' });
		mounted = mount(draft);
		await settle();

		expect(mounted.target.textContent).toContain('could not reach the checkpoint scanner');
		const retry = mounted.target.querySelector('button');
		expect(retry?.textContent?.trim()).toBe('Retry');
	});

	it('renders an unsupported checkpoint as a disabled option naming its reason', async () => {
		vi.mocked(api.api.listNativeCheckpoints).mockResolvedValue(
			ok([
				checkpoint({ name: 'good-one' }),
				checkpoint({ name: 'bad-one', supported: false, reason: 'model_type is not supported', quant_modes: [] })
			])
		);

		const draft = makeDraft({ type: 'native' });
		mounted = mount(draft);
		await settle();

		const select = mounted.target.querySelector('#test-model') as HTMLSelectElement;
		expect(select).toBeTruthy();
		const badOption = Array.from(select.options).find((o) => o.value === 'bad-one');
		expect(badOption?.disabled).toBe(true);
		expect(badOption?.textContent).toContain('model_type is not supported');
		const goodOption = Array.from(select.options).find((o) => o.value === 'good-one');
		expect(goodOption?.disabled).toBe(false);
	});

	it('retains and explains a saved checkpoint that is no longer listed, never silently replacing it', async () => {
		vi.mocked(api.api.listNativeCheckpoints).mockResolvedValue(ok([checkpoint({ name: 'still-here' })]));

		const draft = makeDraft({ type: 'native', model: 'deleted-from-disk' });
		mounted = mount(draft);
		await settle();

		// The value is never reassigned out from under the admin.
		expect(draft.model).toBe('deleted-from-disk');

		const select = mounted.target.querySelector('#test-model') as HTMLSelectElement;
		expect(select.value).toBe('deleted-from-disk');
		const missingOption = Array.from(select.options).find((o) => o.value === 'deleted-from-disk');
		expect(missingOption).toBeTruthy();
		expect(missingOption?.disabled).toBe(false);
		expect(mounted.target.textContent).toContain('is no longer listed under models/llm/');
	});

	it('round-trips the thinking mode through provider_options: null/true/false', async () => {
		vi.mocked(api.api.listNativeCheckpoints).mockResolvedValue(ok([checkpoint()]));

		const draft = makeDraft({ type: 'native', model: 'qwen3-tiny' });
		mounted = mount(draft);
		await settle();

		const select = mounted.target.querySelector('#test-native-thinking') as HTMLSelectElement;
		expect(select).toBeTruthy();
		// Unset by default — never coerced to a stored `false`.
		expect(draft.provider_options.thinking).toBeUndefined();
		expect(select.value).toBe(''); // "Model default" option's value

		select.value = 'true';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		expect(draft.provider_options.thinking).toBe(true);

		select.value = 'false';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		expect(draft.provider_options.thinking).toBe(false);

		// Back to "Model default" deletes the key entirely (never stores `null`).
		select.value = '';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		expect('thinking' in draft.provider_options).toBe(false);
	});

	it('leaves an external provider (ollama) untouched: no checkpoint fetch, plain text model field, no Native Options', async () => {
		const draft = makeDraft({ type: 'ollama', model: 'llama3' });
		mounted = mount(draft);
		await settle();

		expect(api.api.listNativeCheckpoints).not.toHaveBeenCalled();

		const modelField = mounted.target.querySelector('#test-model');
		expect(modelField?.tagName).toBe('INPUT');
		expect((modelField as HTMLInputElement).value).toBe('llama3');

		expect(mounted.target.textContent).not.toContain('Native Options');
		expect(mounted.target.textContent).toContain('Ollama Options');
	});
});

describe('LLMConfigForm — native and Ollama sampling controls', () => {
	function numberInput(target: HTMLElement, id: string) {
		return target.querySelector(`#${id}`) as HTMLInputElement;
	}

	function setValue(input: HTMLInputElement, value: string) {
		input.value = value;
		input.dispatchEvent(new Event('input', { bubbles: true }));
	}

	it('renders the four native sampling fields unset, inserting no guessed value', async () => {
		vi.mocked(api.api.listNativeCheckpoints).mockResolvedValue(ok([checkpoint()]));

		const draft = makeDraft({ type: 'native', model: 'qwen3-tiny' });
		mounted = mount(draft);
		await settle();

		for (const key of ['top_k', 'top_p', 'min_p', 'repetition_penalty']) {
			const input = numberInput(mounted.target, `test-native-${key}`);
			expect(input, `${key} input`).toBeTruthy();
			expect(input.value).toBe('');
			expect(input.placeholder).toBe('auto');
			expect(key in draft.provider_options).toBe(false);
		}
	});

	it('preserves explicit neutral values (top_k=0, min_p=0, repetition_penalty=1) instead of treating them as unset', async () => {
		vi.mocked(api.api.listNativeCheckpoints).mockResolvedValue(ok([checkpoint()]));

		const draft = makeDraft({ type: 'native', model: 'qwen3-tiny' });
		mounted = mount(draft);
		await settle();

		setValue(numberInput(mounted.target, 'test-native-top_k'), '0');
		setValue(numberInput(mounted.target, 'test-native-min_p'), '0');
		setValue(numberInput(mounted.target, 'test-native-repetition_penalty'), '1');
		await settle();

		expect(draft.provider_options.top_k).toBe(0);
		expect(draft.provider_options.min_p).toBe(0);
		expect(draft.provider_options.repetition_penalty).toBe(1);
	});

	it('clearing a native sampling field back to blank removes its override entirely', async () => {
		vi.mocked(api.api.listNativeCheckpoints).mockResolvedValue(ok([checkpoint()]));

		const draft = makeDraft({
			type: 'native',
			model: 'qwen3-tiny',
			provider_options: { top_p: 0.8 }
		});
		mounted = mount(draft);
		await settle();

		const input = numberInput(mounted.target, 'test-native-top_p');
		expect(input.value).toBe('0.8');

		setValue(input, '');
		await settle();

		expect('top_p' in draft.provider_options).toBe(false);
	});

	it('does not change temperature, thinking, quantization, model or default-provider choices when setting a sampling field', async () => {
		vi.mocked(api.api.listNativeCheckpoints).mockResolvedValue(ok([checkpoint()]));

		const draft = makeDraft({
			type: 'native',
			model: 'qwen3-tiny',
			temperature: 0.42,
			provider_options: { thinking: true, quantization: 'int8' }
		});
		mounted = mount(draft);
		await settle();

		setValue(numberInput(mounted.target, 'test-native-top_k'), '20');
		await settle();

		expect(draft.temperature).toBe(0.42);
		expect(draft.model).toBe('qwen3-tiny');
		expect(draft.provider_options.thinking).toBe(true);
		expect(draft.provider_options.quantization).toBe('int8');
	});

	it('renders an unset Ollama Min-P field and lets it be set', async () => {
		const draft = makeDraft({ type: 'ollama', model: 'llama3' });
		mounted = mount(draft);
		await settle();

		const input = numberInput(mounted.target, 'test-ollama-min_p');
		expect(input).toBeTruthy();
		expect(input.value).toBe('');
		expect('min_p' in draft.provider_options).toBe(false);

		setValue(input, '0.05');
		await settle();
		expect(draft.provider_options.min_p).toBe(0.05);
	});

	it('an unrelated provider (openai) renders none of the native or Ollama sampling fields', async () => {
		const draft = makeDraft({ type: 'openai', model: 'gpt-4o' });
		mounted = mount(draft);
		await settle();

		for (const key of ['top_k', 'top_p', 'min_p', 'repetition_penalty']) {
			expect(mounted.target.querySelector(`#test-native-${key}`)).toBeNull();
			expect(mounted.target.querySelector(`#test-ollama-${key}`)).toBeNull();
		}
	});
});
