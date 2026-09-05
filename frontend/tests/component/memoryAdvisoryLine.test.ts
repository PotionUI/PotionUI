// @vitest-environment jsdom
//
// MemoryAdvisoryLine (generation panel): renders the one-line, non-blocking
// GPU memory advisory for the current form, driven by the debounced
// memoryAdvisory.ts controller. Drives the real component with a mocked
// `previewGenerationMemory` and asserts the rendered copy for a known
// estimate, a partially-unknown one, a remote backend, and no preset
// selected at all (nothing rendered).
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
		estimate: { lower_bound_gb: 12.5, weights_gb: 11.36, activation_gb: 1.6, margin: 1.1, basis: 'x' },
		coverage: { known: [{ ref: 'ckpt1', size_gb: 11.36 }], unknown: [], active_set_resolved: true, pinned_components_uncounted: true, uncertainty: [] },
		device: { kind: 'local', free_gb: 20, total_gb: 24, provenance: "this host's GPU monitor" },
		budget: { configured_gb: 24, source: 'backend gpu_max_vram + device free VRAM' },
		backend: { id: 'b1', name: 'Local', engine: 'native', driver: 'native.local' }
	};
}

function exceedsBudget(): MemoryPreviewResult {
	const base = known();
	return { ...base, budget: { ...base.budget, configured_gb: 8 } };
}

function partiallyUnknown(): MemoryPreviewResult {
	return {
		estimate: { lower_bound_gb: null, weights_gb: 0, activation_gb: 0, margin: 1.1, basis: 'x' },
		coverage: { known: [], unknown: ['ckpt1', 'lora1'], active_set_resolved: true, pinned_components_uncounted: true, uncertainty: ['2 referenced model(s) have no indexed file size and are excluded from the lower bound.'] },
		device: { kind: 'local', free_gb: 20, total_gb: 24, provenance: "this host's GPU monitor" },
		budget: { configured_gb: 24, source: 'x' },
		backend: { id: 'b1', name: 'Local', engine: 'native', driver: 'native.local' }
	};
}

function remoteBackend(): MemoryPreviewResult {
	return {
		estimate: { lower_bound_gb: 12.5, weights_gb: 11.36, activation_gb: 1.6, margin: 1.1, basis: 'x' },
		coverage: { known: [{ ref: 'ckpt1', size_gb: 11.36 }], unknown: [], active_set_resolved: true, pinned_components_uncounted: true, uncertainty: [] },
		device: { kind: 'remote', free_gb: null, total_gb: null, provenance: 'not reported by the remote worker' },
		budget: { configured_gb: null, source: 'not configured' },
		backend: { id: 'r1', name: 'Remote', engine: 'native', driver: 'native.remote' }
	};
}

function undeclaredExecutionDeviceBackend(): MemoryPreviewResult {
	return {
		estimate: { lower_bound_gb: 12.5, weights_gb: 11.36, activation_gb: 1.6, margin: 1.1, basis: 'x' },
		coverage: { known: [{ ref: 'ckpt1', size_gb: 11.36 }], unknown: [], active_set_resolved: true, pinned_components_uncounted: true, uncertainty: [] },
		device: { kind: 'unknown', free_gb: null, total_gb: null, provenance: 'execution device not declared by this backend' },
		budget: { configured_gb: null, source: 'not configured' },
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

	it('renders the estimate and device evidence for a fully known result', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		vi.mocked(api.api.previewGenerationMemory).mockResolvedValue(response(known()));

		mounted = mount({});
		await vi.advanceTimersByTimeAsync(400);

		const text = mounted.line()?.textContent ?? '';
		expect(text).toContain('Estimated at least ~12.5 GB');
		expect(text).toContain('1 of 1 model size known');
		expect(text).toContain('this GPU reports 20.0 GB free of 24.0 GB');
		expect(mounted.line()?.className).toContain('text-fg-subtle');
	});

	it('warns (text-warning) when the estimate exceeds the configured budget', async () => {
		vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
		vi.mocked(api.api.previewGenerationMemory).mockResolvedValue(response(exceedsBudget()));

		mounted = mount({});
		await vi.advanceTimersByTimeAsync(400);

		const text = mounted.line()?.textContent ?? '';
		expect(text).toContain('may exceed the configured VRAM budget');
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

	it('reports a remote backend without reading this host\'s GPU numbers', async () => {
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
});
