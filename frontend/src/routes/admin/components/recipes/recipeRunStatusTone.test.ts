import { describe, expect, it } from 'vitest';
import { runStatusTone } from './recipeRunStatusTone';

describe('runStatusTone', () => {
	it('maps each run status to a table status tone', () => {
		expect(runStatusTone('completed')).toBe('success');
		expect(runStatusTone('failed')).toBe('danger');
		expect(runStatusTone('cancelled')).toBe('muted');
		expect(runStatusTone('paused')).toBe('warning');
		expect(runStatusTone('awaiting_consent')).toBe('signal');
		expect(runStatusTone('running')).toBe('info');
	});
});
