import { describe, it, expect, vi, beforeEach } from 'vitest';

const put = vi.fn();
const get = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: { getClient: () => ({ put, get }) }
}));

const { setEngineFlags, getBackendEngineFlags } = await import('./admin-api');

beforeEach(() => {
	put.mockReset().mockResolvedValue({ data: { success: true, data: { engine_flags: {} } } });
	get.mockReset().mockResolvedValue({ data: { success: true, data: { engine_flags: {}, is_local: false } } });
});

describe('setEngineFlags', () => {
	it('PUTs the changed knobs wrapped in flags, keeping booleans, strings and numbers typed', async () => {
		await setEngineFlags('b1', {
			native_fp8_matmul: true,
			native_fp8_quantize: 'force',
			native_min_inference_memory_gb: 2.5,
			native_ltx_decode_tile_px: 512
		});
		expect(put).toHaveBeenCalledWith('/api/backends/b1/optimizations/engine-flags', {
			flags: {
				native_fp8_matmul: true,
				native_fp8_quantize: 'force',
				native_min_inference_memory_gb: 2.5,
				native_ltx_decode_tile_px: 512
			}
		});
		const body = put.mock.calls[0][1].flags;
		expect(typeof body.native_fp8_matmul).toBe('boolean');
		expect(typeof body.native_min_inference_memory_gb).toBe('number');
	});

	it('returns the resolved flags the server sends back', async () => {
		put.mockResolvedValue({ data: { success: true, data: { engine_flags: { native_lora_fused: false } } } });
		const response = await setEngineFlags('b1', { native_lora_fused: false });
		expect(response.data?.engine_flags).toEqual({ native_lora_fused: false });
	});
});

describe('getBackendEngineFlags', () => {
	it('reads the engine flags of any native backend', async () => {
		const response = await getBackendEngineFlags('w1');
		expect(get).toHaveBeenCalledWith('/api/backends/w1/engine-flags');
		expect(response.data?.is_local).toBe(false);
	});
});
