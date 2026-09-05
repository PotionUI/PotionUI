import { describe, expect, it } from 'vitest';
import { describeLoraDiagnostic } from './loraApplicationDiagnostics';

describe('describeLoraDiagnostic', () => {
	it('is null for a plain requested-stack entry (no diagnostic fields at all)', () => {
		expect(describeLoraDiagnostic({})).toBeNull();
	});

	it('is null for a fully-matched adapter with nothing ignored', () => {
		expect(
			describeLoraDiagnostic({ zero_effect: false, unmatched_keys: 0, ignored: [] })
		).toBeNull();
	});

	it('reports a zero-effect adapter as danger / "No effect"', () => {
		const diag = describeLoraDiagnostic({
			zero_effect: true,
			unmatched_keys: 1,
			unmatched_sample: ['lora_unet_totally_bogus']
		});
		expect(diag).not.toBeNull();
		expect(diag?.tone).toBe('danger');
		expect(diag?.label).toBe('No effect');
		expect(diag?.reason).toBe('1 key unmatched');
		expect(diag?.unmatchedSample).toEqual(['lora_unet_totally_bogus']);
	});

	it('reports a partially-unmatched (but non-zero) adapter as warning', () => {
		const diag = describeLoraDiagnostic({ zero_effect: false, unmatched_keys: 3 });
		expect(diag?.tone).toBe('warning');
		expect(diag?.label).toBe('Partially applied');
		expect(diag?.reason).toBe('3 keys unmatched');
	});

	it('reports an ignored contribution (e.g. a DoRA magnitude) even with zero unmatched keys', () => {
		const diag = describeLoraDiagnostic({ zero_effect: false, unmatched_keys: 0, ignored: ['dora_scale×1'] });
		expect(diag).not.toBeNull();
		expect(diag?.tone).toBe('warning');
		expect(diag?.reason).toBe('dora_scale×1');
	});

	it('combines unmatched and ignored reasons', () => {
		const diag = describeLoraDiagnostic({
			zero_effect: false,
			unmatched_keys: 2,
			ignored: ['dora_scale×1']
		});
		expect(diag?.reason).toBe('2 keys unmatched · dora_scale×1');
	});

	it('labels a window-not-reached adapter distinctly from an unmatched-key one', () => {
		const diag = describeLoraDiagnostic({
			zero_effect: true,
			unmatched_keys: 0,
			reason: 'window_not_reached'
		});
		expect(diag).not.toBeNull();
		expect(diag?.tone).toBe('danger');
		expect(diag?.label).toBe('No effect');
		expect(diag?.reason).toBe("selected step window never overlapped the run's steps");
	});
});
