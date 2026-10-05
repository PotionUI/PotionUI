import { describe, it, expect } from 'vitest';
import {
	RUNTIME_SETTINGS,
	appliesLabel,
	changedKeysSince,
	parseRuntimeNumber,
	restartPendingKeys,
	runtimeSetting,
	runtimeSettingKeys,
	runtimeSettingValue,
	runtimeSettingsFor
} from './runtimeSettings';
import { SETTINGS_GROUPS, SETTINGS_KEY_GROUP } from './settingsGroups';

const descriptor = (key: string) => runtimeSetting(key)!;

describe('RUNTIME_SETTINGS', () => {
	it('declares each key once', () => {
		const keys = RUNTIME_SETTINGS.map((d) => d.key);
		expect(new Set(keys).size).toBe(keys.length);
	});

	it('gives every default the type its kind sends in the PUT body', () => {
		const expected = { bool: 'boolean', choice: 'string', float: 'number', int: 'number' };
		for (const d of RUNTIME_SETTINGS) {
			expect(typeof d.defaultValue, d.key).toBe(expected[d.kind]);
		}
	});

	it('lists the default among the choices of every choice setting', () => {
		for (const d of RUNTIME_SETTINGS.filter((s) => s.kind === 'choice')) {
			expect(d.choices?.map((c) => c.value), d.key).toContain(d.defaultValue);
		}
	});

	it('points every parent at a boolean setting', () => {
		for (const d of RUNTIME_SETTINGS.filter((s) => s.parent)) {
			expect(runtimeSetting(d.parent!)?.kind, d.key).toBe('bool');
		}
	});

	it('scopes the diagnostics settings to the app and every engine knob to a backend', () => {
		for (const d of RUNTIME_SETTINGS) {
			expect(d.scope, d.key).toBe(d.group === 'diagnostics' ? 'app' : 'backend');
		}
	});

	it('maps the app settings to Diagnostics, so the System Settings save bar sends them', () => {
		for (const key of runtimeSettingKeys('app')) {
			expect(SETTINGS_KEY_GROUP[key], key).toBe('diagnostics');
		}
		expect(Object.entries(SETTINGS_KEY_GROUP).filter(([, g]) => g === 'diagnostics').map(([k]) => k)).toEqual(
			runtimeSettingKeys('app')
		);
	});

	it('keeps every engine knob out of the System Settings save body', () => {
		for (const key of runtimeSettingKeys('backend')) {
			expect(SETTINGS_KEY_GROUP[key], key).toBeUndefined();
		}
	});

	it('places Diagnostics right after Generation and has no Performance group', () => {
		const ids: string[] = SETTINGS_GROUPS.map((g) => g.id);
		expect(ids[ids.indexOf('generation') + 1]).toBe('diagnostics');
		expect(ids).not.toContain('performance');
	});
});

describe('runtimeSettingKeys', () => {
	it('lists the engine keys a native backend stores', () => {
		expect(runtimeSettingKeys('backend')).toEqual([
			'native_fp8_matmul',
			'native_nvfp4_matmul',
			'native_lora_fused',
			'native_torch_compile',
			'native_qwen3_te_bf16',
			'native_sol_attn_backend',
			'native_attention_backend',
			'native_stream_prefetch',
			'native_fp8_quantize',
			'native_min_inference_memory_gb',
			'native_ltx_decode_tile_px',
			'native_ltx_decode_tile_frames',
			'native_sol_attn_debug'
		]);
	});

	it('defaults the attention backend to automatic', () => {
		expect(runtimeSetting('native_attention_backend')?.defaultValue).toBe('');
		expect(runtimeSetting('native_attention_backend')?.choices?.map((c) => c.value)).toEqual([
			'',
			'sdpa',
			'sage',
			'sage2',
			'sage3',
			'flash',
			'sparge'
		]);
	});
});

describe('runtimeSettingsFor', () => {
	it('returns the settings of one section in declaration order', () => {
		expect(runtimeSettingsFor('diagnostics').map((d) => d.key)).toEqual(['profiling_enabled', 'profiling_census']);
		expect(runtimeSettingsFor('debug').map((d) => d.key)).toEqual(['native_sol_attn_debug']);
	});

	it('splits speed and memory without overlap', () => {
		const speed = runtimeSettingsFor('speed').map((d) => d.key);
		const memory = runtimeSettingsFor('memory').map((d) => d.key);
		expect(speed).toContain('native_sol_attn_backend');
		expect(speed).toContain('native_attention_backend');
		expect(memory).toContain('native_min_inference_memory_gb');
		expect(speed.filter((k) => memory.includes(k))).toEqual([]);
	});
});

describe('appliesLabel', () => {
	it('says nothing for live settings', () => {
		expect(appliesLabel('live')).toBe('');
	});

	it('names when next_load and restart settings take effect', () => {
		expect(appliesLabel('next_load')).toBe('Applies on next model load');
		expect(appliesLabel('restart')).toBe('Applies after restart');
	});
});

describe('restartPendingKeys', () => {
	it('keeps only keys that apply after a restart', () => {
		expect(restartPendingKeys(['native_fp8_matmul', 'native_sol_attn_backend', 'native_torch_compile'])).toEqual([
			'native_sol_attn_backend'
		]);
	});

	it('de-duplicates and ignores unknown keys', () => {
		expect(restartPendingKeys(['native_sol_attn_backend', 'native_sol_attn_backend', 'nope'])).toEqual([
			'native_sol_attn_backend'
		]);
	});

	it('returns nothing when no restart setting changed', () => {
		expect(restartPendingKeys([])).toEqual([]);
		expect(restartPendingKeys(['profiling_enabled'])).toEqual([]);
	});
});

describe('runtimeSettingValue', () => {
	it('falls back to the default when the value is missing', () => {
		expect(runtimeSettingValue(descriptor('native_lora_fused'), {})).toBe(true);
		expect(runtimeSettingValue(descriptor('native_fp8_quantize'), { native_fp8_quantize: null })).toBe('auto');
		expect(runtimeSettingValue(descriptor('native_min_inference_memory_gb'), {})).toBe(1.0);
	});

	it('keeps a stored false rather than replacing it with a true default', () => {
		expect(runtimeSettingValue(descriptor('profiling_census'), { profiling_census: false })).toBe(false);
	});

	it('reads numeric strings as numbers and rejects garbage', () => {
		const d = descriptor('native_ltx_decode_tile_px');
		expect(runtimeSettingValue(d, { native_ltx_decode_tile_px: '512' })).toBe(512);
		expect(runtimeSettingValue(d, { native_ltx_decode_tile_px: 'abc' })).toBe(0);
	});
});

describe('parseRuntimeNumber', () => {
	it('keeps fractions for float settings', () => {
		expect(parseRuntimeNumber(descriptor('native_min_inference_memory_gb'), '2.5')).toBe(2.5);
	});

	it('rounds int settings', () => {
		expect(parseRuntimeNumber(descriptor('native_ltx_decode_tile_frames'), '16.4')).toBe(16);
	});

	it('returns null for an empty or non-numeric field', () => {
		const d = descriptor('native_ltx_decode_tile_px');
		expect(parseRuntimeNumber(d, '')).toBeNull();
		expect(parseRuntimeNumber(d, '  ')).toBeNull();
		expect(parseRuntimeNumber(d, 'abc')).toBeNull();
	});
});

describe('changedKeysSince', () => {
	it('lists keys whose value differs from the baseline', () => {
		expect(
			changedKeysSince(
				['native_sol_attn_backend', 'native_fp8_matmul'],
				{ native_sol_attn_backend: 'kernel', native_fp8_matmul: false },
				{ native_sol_attn_backend: 'flex', native_fp8_matmul: false }
			)
		).toEqual(['native_sol_attn_backend']);
	});

	it('treats two missing values as unchanged', () => {
		expect(changedKeysSince(['native_sol_attn_backend'], {}, {})).toEqual([]);
	});
});
