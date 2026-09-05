// @vitest-environment jsdom
//
// MemoryAdvisoryLine (generation panel): renders the one-line, non-blocking
// GPU memory advisory for the current form, driven by the debounced
// memoryAdvisory.ts controller. Drives the real component with a mocked
// `previewGenerationMemory` and asserts the rendered copy for a known
// estimate, a partially-unknown one, an unresolved active set, a remote
// backend, and no preset selected at all (nothing rendered).
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { MemoryPreviewResult } from '$lib/types/api';

vi.mock('$lib/services/api', () => ({
	api: { previewGenerationMemory: vi.fn() }
}));

const api = await import('$lib/services/api');
const { default: MemoryAdvisoryLine } = await import(
	'../../src/lib/components/generation-panel/MemoryAdvisoryLine.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function response(data: MemoryPreviewResult) {
	return { success: true, data };
}

function known(): MemoryPreviewResult {
	return {
		estimate: { checkpoint_estimate_gb: 12.5, weights_gb: 11.36, activation_gb: 1.6, margin: 1.1, basis: 'x' },
		coverage: { known: [{ ref: 'ckpt1', size_gb: 11.36 }], unknown: [], active_set_resolved: true, pinned_components_uncounted: true, uncertainty: [] },
		device: { kind: 'local', free_gb: 20, total_gb: 24, provenance: "this host's GPU monitor" },
		budget: { configured_gb: 24, source: 'backend gpu_max_vram + device free VRAM', pipe_hints_gb: [] },
		backend: { id: 'b1', name: 'Local', engine: 'native', driver: 'native.local' }
	};
}

function exceedsBudget(): MemoryPreviewResult {
	const base = known();
	return { ...base, budget: { ...base.budget, configured_gb: 8 } };
}

function withPipeHints(): MemoryPreviewResult {
	const base = known();
	return {
		...base,
		budget: {
			...base.budget,
			pipe_hints_gb: [
				{ pipe: 'detailer1', hint_gb: 8, composed_gb: 8 },
				{ pipe: 'loader1', hint_gb: 32, composed_gb: 24 }
			]
		}
	};
}

function partiallyUnknown(): MemoryPreviewResult {
	return {
		estimate: { checkpoint_estimate_gb: null, weights_gb: 0, activation_gb: 0, margin: 1.1, basis: 'x' },
		coverage: { known: [], unknown: ['ckpt1', 'lora1'], active_set_resolved: true, pinned_components_uncounted: true, uncertainty: ['2 referenced model(s) have no indexed file size and are excluded from the estimate.'] },
		device: { kind: 'local', free_gb: 20, total_gb: 24, provenance: "this host's GPU monitor" },
		budget: { configured_gb: 24, source: 'x', pipe_hints_gb: [] },
		backend: { id: 'b1', name: 'Local', engine: 'native', driver: 'native.local' }
	};
}

function unresolvedActiveSet(): MemoryPreviewResult {
	return {
		estimate: { checkpoint_estimate_gb: null, weights_gb: 0, activation_gb: 0, margin: 1.1, basis: 'x' },
		coverage: { known: [], unknown: [], active_set_resolved: false, pinned_components_uncounted: true, uncertainty: ['The active model set could not be resolved for this form (pipeline build failed), so no model sizes are counted.'] },
		device: { kind: 'local', free_gb: 20, total_gb: 24, provenance: "this host's GPU monitor" },
		budget: { configured_gb: 24, source: 'x', pipe_hints_gb: [] },
		backend: { id: 'b1', name: 'Local', engine: 'native', driver: 'native.local' }
	};
}

function remoteBackend(): MemoryPreviewResult {
	return {
		estimate: { checkpoint_estimate_gb: 12.5, weights_gb: 11.36, activation_gb: 1.6, margin: 1.1, basis: 'x' },
		coverage: { known: [{ ref: 'ckpt1', size_gb: 11.36 }], unknown: [], active_set_resolved: true, pinned_components_uncounted: true, uncertainty: [] },
		device: { kind: 'remote', free_gb: null, total_gb: null, provenance: 'not reported by the remote worker' },
		budget: { configured_gb: null, source: 'not configured', pipe_hints_gb: [] },
		backend: { id: 'r1', name: 'Remote', engine: 'native', driver: 'native.remote' }
	};
}

function undeclaredExecutionDeviceBackend(): MemoryPreviewResult {
	return {
		estimate: { checkpoint_estimate_gb: 12.5, weights_gb: 11.36, activation_gb: 1.6, margin: 1.1, basis: 'x' },
		coverage: { known: [{ ref: 'ckpt1', size_gb: 11.36 }], unknown: [], active_set_resolved: true, pinned_components_uncounted: true, uncertainty: [] },
		device: { kind: 'unknown', free_gb: null, total_gb: null, provenance: 'execution device not declared by this backend' },
		budget: { configured_gb: null, source: 'not configured', pipe_hints_gb: [] },
		backend: { id: 'comfy_1', name: 'ComfyUI', engine: 'comfyui', driver: 'comfyui' }
	};
}

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: MemoryAdvisoryLine as never,
		target,
		props: { presetId: 'preset_1', formData: { steps: 20 }, ...props }
	});
	return {
		target,
		line: () => target.querySelector('.memory-advisory-line'),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	vi.useRealTimers();
});

describe('MemoryAdvisoryLine', () => {
	it('renders nothing while no preset is selected', async () => {
		mounted = mount({ presetId: null });
		await vi.waitFor(() => {}, { timeout: 1 }).catch(() => {});
		expect(mounted.line()).toBeNull();
		expect(api.api.previewGenerationMemory).not.toHaveBeenCalled();
	});

	it('renders the checkpoint-based estimate and device evidence, never "lower bound" or "at least"', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		vi.mocked(api.api.previewGenerationMemory).mockResolvedValue(response(known()));

		mounted = mount({});
		await vi.advanceTimersByTimeAsync(400);

		const text = mounted.line()?.textContent ?? '';
		expect(text).toContain('Checkpoint-based estimate ~12.5 GB');
		expect(text).toContain('1 of 1 model size known');
		expect(text).toContain('runtime use may be lower with quantization or streaming and higher for uncounted components');
		expect(text).toContain('this GPU reports 20.0 GB free of 24.0 GB');
		expect(text.toLowerCase()).not.toContain('lower bound');
		expect(text.toLowerCase()).not.toContain('at least');
		expect(mounted.line()?.className).toContain('text-fg-subtle');
	});

	it('warns (text-warning) when the estimate is higher than the configured budget', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		vi.mocked(api.api.previewGenerationMemory).mockResolvedValue(response(exceedsBudget()));

		mounted = mount({});
		await vi.advanceTimersByTimeAsync(400);

		const text = mounted.line()?.textContent ?? '';
		expect(text).toContain('may be higher than the configured VRAM budget');
		expect(mounted.line()?.className).toContain('text-warning');
	});

	it('renders an unavailable message when no referenced model size is known', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		vi.mocked(api.api.previewGenerationMemory).mockResolvedValue(response(partiallyUnknown()));

		mounted = mount({});
		await vi.advanceTimersByTimeAsync(400);

		const text = mounted.line()?.textContent ?? '';
		expect(text).toContain('Estimate unavailable: none of 2 referenced model sizes are indexed');
	});

	it('reports the request as unresolved, never "no model references", when the active set could not be built', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		vi.mocked(api.api.previewGenerationMemory).mockResolvedValue(response(unresolvedActiveSet()));

		mounted = mount({});
		await vi.advanceTimersByTimeAsync(400);

		const text = mounted.line()?.textContent ?? '';
		expect(text).toContain('Estimate unavailable: the request could not be resolved');
		expect(text).not.toContain('no model references');
	});

	it("reports a remote backend without reading this host's GPU numbers", async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		vi.mocked(api.api.previewGenerationMemory).mockResolvedValue(response(remoteBackend()));

		mounted = mount({});
		await vi.advanceTimersByTimeAsync(400);

		const text = mounted.line()?.textContent ?? '';
		expect(text).toContain('remote backend: memory not reported');
	});

	it('reports unknown GPU visibility for a backend with no declared execution device', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		vi.mocked(api.api.previewGenerationMemory).mockResolvedValue(response(undeclaredExecutionDeviceBackend()));

		mounted = mount({});
		await vi.advanceTimersByTimeAsync(400);

		const text = mounted.line()?.textContent ?? '';
		expect(text).toContain('GPU visibility unknown for this backend');
	});

	it('shows per-stage pipe hints in the tooltip, scoped separately from the backend budget', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		vi.mocked(api.api.previewGenerationMemory).mockResolvedValue(response(withPipeHints()));

		mounted = mount({});
		await vi.advanceTimersByTimeAsync(400);

		mounted.line()!.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
		await vi.advanceTimersByTimeAsync(200);

		const tooltip = Array.from(document.body.querySelectorAll('div')).find((el) =>
			el.textContent?.includes('Per-stage hints')
		);
		expect(tooltip, 'tooltip with per-stage hints did not render').toBeTruthy();
		expect(tooltip!.textContent).toContain('detailer1=8.0 GB');
		expect(tooltip!.textContent).toContain('loader1=32.0 GB (effective 24.0 GB)');
		expect(tooltip!.textContent).toContain('not merged into the budget above');
	});
});
