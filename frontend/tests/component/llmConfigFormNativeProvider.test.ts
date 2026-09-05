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
