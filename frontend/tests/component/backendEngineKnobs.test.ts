import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/admin-api', () => ({
	getBackendOptimizations: vi.fn(),
	getBackendEngineFlags: vi.fn(),
	setEngineFlags: vi.fn(),
	setAttentionBackend: vi.fn(),
	installBackendOptimization: vi.fn(),
	getCurrentOptimizationJob: vi.fn(),
	cancelCurrentOptimizationJob: vi.fn(),
	restartApp: vi.fn(() => Promise.resolve({ success: true })),
	runOptimizationBenchmark: vi.fn()
}));
vi.mock('$lib/stores/toast', () => ({
	toasts: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }
}));

const adminApi = await import('$lib/services/admin-api');
const { toasts } = await import('$lib/stores/toast');
const { default: BackendOptimizations } = await import(
	'../../src/routes/admin/components/BackendOptimizations.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

const FLAGS = {
	native_fp8_matmul: false,
	native_nvfp4_matmul: false,
	native_lora_fused: true,
	native_torch_compile: false,
	native_qwen3_te_bf16: false,
	native_sol_attn_backend: 'flex',
	native_attention_backend: '',
	native_stream_prefetch: false,
	native_fp8_quantize: 'auto',
	native_min_inference_memory_gb: 1.0,
	native_ltx_decode_tile_px: 0,
	native_ltx_decode_tile_frames: 0,
	native_sol_attn_debug: false
};

function localPayload(flags: Record<string, unknown> = FLAGS) {
	return {
		success: true,
		data: {
			system: {
				cuda_available: true,
				compute_capability: [8, 9],
				gpu_name: 'Test GPU',
				gpu_vram_gb: 24,
				torch_version: '2.9.0',
				torch_cuda_version: '12.8',
				nvcc_found: true,
				nvcc_version: [12, 8],
				nvcc_cuda_matches_torch: true,
				nvcc_source: 'system',
				gcc_found: true,
				python_h_found: true,
				sageattention_version: null,
				triton_version: null,
				flash_attn_version: null,
				xformers_version: null,
				active_backend: 'sdpa',
				available_backends: ['sdpa']
			},
			optimizations: [
				{
					opt_id: 'sage',
					name: 'SageAttention',
					description: 'Faster attention',
					benefit: 'Up to 2x',
					needs_restart: true,
					installed: false,
					installed_version: null,
					active: false,
					installable: true,
					requirements: []
				}
			],
			pinned_backend: null,
			engine_flags: flags
		}
	};
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

async function mountLocal(flags: Record<string, unknown> = FLAGS) {
	vi.mocked(adminApi.getBackendOptimizations).mockResolvedValue(localPayload(flags) as never);
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({ component: BackendOptimizations as never, target, props: { backendId: 'b1' } });
	await settle();
	return target;
}

async function mountRemote(flags: Record<string, unknown> = FLAGS) {
	vi.mocked(adminApi.getBackendEngineFlags).mockResolvedValue({
		success: true,
		data: { engine_flags: flags, is_local: false }
	} as never);
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({
		component: BackendOptimizations as never,
		target,
		props: { backendId: 'w1', local: false }
	});
	await settle();
	return target;
}

function row(target: HTMLElement, key: string): HTMLElement {
	const el = target.querySelector<HTMLElement>(`[data-testid="runtime-setting-${key}"]`);
	if (!el) throw new Error(`row ${key} not rendered`);
	return el;
}

function sectionLabels(target: HTMLElement): string[] {
	return [...target.querySelectorAll('section h3')].map((h) => h.textContent?.trim() ?? '');
}

function savedResponse(patch: Record<string, unknown>) {
	return { success: true, data: { engine_flags: { ...FLAGS, ...patch } } } as never;
}

afterEach(() => {
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('BackendOptimizations engine knobs on the local backend', () => {
	it('renders Speed, Memory and Debug logging next to the system probe, attention pin and install catalog', async () => {
		const target = await mountLocal();
		expect(sectionLabels(target)).toEqual([
			'System',
			'Attention backend',
			'Speed',
			'Memory',
			'Debug logging',
			'Optimizations'
		]);
		expect(target.textContent).not.toContain('Engine flags');
	});

	it('keeps the attention backend in its pin section rather than repeating it as a knob', async () => {
		const target = await mountLocal();
		expect(target.querySelector('[data-testid="runtime-setting-native_attention_backend"]')).toBeNull();
	});

	it('saves a switch at once with only the changed key as a boolean', async () => {
		vi.mocked(adminApi.setEngineFlags).mockResolvedValue(savedResponse({ native_fp8_matmul: true }));
		const target = await mountLocal();
		row(target, 'native_fp8_matmul').querySelector<HTMLInputElement>('input[role="switch"]')!.click();
		await settle();
		expect(adminApi.setEngineFlags).toHaveBeenCalledWith('b1', { native_fp8_matmul: true });
		expect(row(target, 'native_fp8_matmul').querySelector<HTMLInputElement>('input[role="switch"]')!.checked).toBe(
			true
		);
	});

	it('saves a number field once it is committed, as a number', async () => {
		vi.mocked(adminApi.setEngineFlags).mockResolvedValue(savedResponse({ native_min_inference_memory_gb: 2.5 }));
		const target = await mountLocal();
		const input = row(target, 'native_min_inference_memory_gb').querySelector<HTMLInputElement>('input')!;
		input.value = '2.5';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(adminApi.setEngineFlags).not.toHaveBeenCalled();
		input.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		expect(adminApi.setEngineFlags).toHaveBeenCalledWith('b1', { native_min_inference_memory_gb: 2.5 });
	});

	it('does not save an empty number field and puts the saved value back', async () => {
		const target = await mountLocal();
		const input = row(target, 'native_ltx_decode_tile_px').querySelector<HTMLInputElement>('input')!;
		input.value = '';
		input.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		expect(adminApi.setEngineFlags).not.toHaveBeenCalled();
		expect(input.value).toBe('0');
	});

	it('saves a choice as its value string', async () => {
		vi.mocked(adminApi.setEngineFlags).mockResolvedValue(savedResponse({ native_fp8_quantize: 'force' }));
		const target = await mountLocal();
		[...row(target, 'native_fp8_quantize').querySelectorAll('button')]
			.find((b) => b.textContent?.trim() === 'Always')!
			.click();
		await settle();
		expect(adminApi.setEngineFlags).toHaveBeenCalledWith('b1', { native_fp8_quantize: 'force' });
	});

	it('labels when next_load and restart knobs take effect', async () => {
		const target = await mountLocal();
		expect(row(target, 'native_torch_compile').textContent).toContain('Applies on next model load');
		expect(row(target, 'native_fp8_quantize').textContent).toContain('Applies on next model load');
		expect(row(target, 'native_sol_attn_backend').textContent).toContain('Applies after restart');
		expect(row(target, 'native_lora_fused').textContent).not.toContain('Applies');
		expect(row(target, 'native_lora_fused').textContent).toContain('Default: On');
	});

	it('shows the error and leaves the switch where it was when the save fails', async () => {
		vi.mocked(adminApi.setEngineFlags).mockResolvedValue({ success: false, message: 'nope' } as never);
		const target = await mountLocal();
		row(target, 'native_fp8_matmul').querySelector<HTMLInputElement>('input[role="switch"]')!.click();
		await settle();
		expect(toasts.error).toHaveBeenCalledWith('nope');
		expect(row(target, 'native_fp8_matmul').querySelector<HTMLInputElement>('input[role="switch"]')!.checked).toBe(
			false
		);
	});

	it('asks for a PotionUI restart with a button once the Sol-Attn implementation changes', async () => {
		vi.mocked(adminApi.setEngineFlags).mockResolvedValue(savedResponse({ native_sol_attn_backend: 'kernel' }));
		const target = await mountLocal();
		expect(target.querySelector('[data-testid="engine-restart-hint"]')).toBeNull();
		[...row(target, 'native_sol_attn_backend').querySelectorAll('button')]
			.find((b) => b.textContent?.trim() === 'Kernel')!
			.click();
		await settle();
		const hint = target.querySelector('[data-testid="engine-restart-hint"]')!;
		expect(hint.textContent).toContain('Restart PotionUI to apply the settings marked Applies after restart.');
		expect([...hint.querySelectorAll('button')].some((b) => b.textContent?.includes('Restart now'))).toBe(true);
	});
});

describe('BackendOptimizations engine knobs on a remote worker', () => {
	it('renders only the knob sections and the worker intro line', async () => {
		const target = await mountRemote();
		expect(adminApi.getBackendOptimizations).not.toHaveBeenCalled();
		expect(adminApi.getBackendEngineFlags).toHaveBeenCalledWith('w1');
		expect(sectionLabels(target)).toEqual(['Speed', 'Memory', 'Debug logging']);
		expect(target.textContent).toContain(
			"These values are sent to the worker with every job, so the worker's own environment never decides them."
		);
		expect(target.textContent).not.toContain('Benchmark');
		expect(target.textContent).not.toContain('Install');
	});

	it('offers the attention backend as a select that saves the backend name', async () => {
		vi.mocked(adminApi.setEngineFlags).mockResolvedValue(savedResponse({ native_attention_backend: 'sage2' }));
		const target = await mountRemote();
		const select = row(target, 'native_attention_backend').querySelector<HTMLSelectElement>('select')!;
		expect([...select.options].map((o) => o.value)).toEqual(['', 'sdpa', 'sage', 'sage2', 'sage3', 'flash', 'sparge']);
		expect(select.value).toBe('');
		select.value = 'sage2';
		select.dispatchEvent(new Event('change', { bubbles: true }));
		await settle();
		expect(adminApi.setEngineFlags).toHaveBeenCalledWith('w1', { native_attention_backend: 'sage2' });
	});

	it('asks for a worker restart without a restart button', async () => {
		vi.mocked(adminApi.setEngineFlags).mockResolvedValue(savedResponse({ native_sol_attn_backend: 'kernel' }));
		const target = await mountRemote();
		[...row(target, 'native_sol_attn_backend').querySelectorAll('button')]
			.find((b) => b.textContent?.trim() === 'Kernel')!
			.click();
		await settle();
		const hint = target.querySelector('[data-testid="engine-restart-hint"]')!;
		expect(hint.textContent?.trim()).toContain('Restart the worker to apply.');
		expect(hint.querySelectorAll('button')).toHaveLength(0);
	});

	it('saves a debug switch as a boolean', async () => {
		vi.mocked(adminApi.setEngineFlags).mockResolvedValue(savedResponse({ native_sol_attn_debug: true }));
		const target = await mountRemote();
		row(target, 'native_sol_attn_debug').querySelector<HTMLInputElement>('input[role="switch"]')!.click();
		await settle();
		expect(adminApi.setEngineFlags).toHaveBeenCalledWith('w1', { native_sol_attn_debug: true });
	});
});
