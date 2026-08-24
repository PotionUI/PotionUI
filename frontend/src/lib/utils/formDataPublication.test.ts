import { describe, expect, it } from 'vitest';
import { shouldPublishFormData } from './formDataPublication';

describe('shouldPublishFormData', () => {
	it('publishes a new flattened payload once, even when callback/reactivity supplies a new object', () => {
		const first = { steps: 31, model: 'model-a' };
		const key = JSON.stringify(first);
		expect(shouldPublishFormData(null, first)).toBe(true);
		expect(shouldPublishFormData(key, { steps: 31, model: 'model-a' })).toBe(false);
	});

	it('publishes the next actual edit immediately', () => {
		const key = JSON.stringify({ steps: 31 });
		expect(shouldPublishFormData(key, { steps: 32 })).toBe(true);
	});

	it('publishes the same payload again for a new schema generation after its key resets', () => {
		const payload = { steps: 31 };
		const priorSchemaKey = JSON.stringify(payload);
		expect(shouldPublishFormData(priorSchemaKey, payload)).toBe(false);
		// DynamicForm resets lastPublishedFormDataKey when preset/mode/variant
		// changes (and for forceReload), creating this new schema generation.
		expect(shouldPublishFormData(null, payload)).toBe(true);
	});

	it('publishes a completed empty normalized form exactly once', () => {
		const empty = {};
		expect(shouldPublishFormData(null, empty)).toBe(true);
		expect(shouldPublishFormData(JSON.stringify(empty), {})).toBe(false);
	});
});
