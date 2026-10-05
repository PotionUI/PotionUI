import { describe, it, expect } from 'vitest';
import { carrySharedBackendFields } from './backendFormDraft';

describe('carrySharedBackendFields', () => {
	it('keeps what the admin typed that every engine shares and takes the rest from the new engine', () => {
		const previous = {
			name: 'My cloud',
			enabled: false,
			priority: 5,
			scheduling_policy: 'round_robin',
			scheduling_max_consecutive_same_model: 2,
			engine: 'native',
			driver: 'native.remote',
			base_url: 'http://worker:26730',
			timeout_seconds: 300
		};
		const next = {
			name: '',
			enabled: true,
			priority: 1,
			scheduling_policy: 'fifo',
			scheduling_max_consecutive_same_model: 3,
			engine: 'cloud',
			driver: 'cloud.fake',
			api_key: '',
			timeout_seconds: 1800
		};

		expect(carrySharedBackendFields(previous, next)).toEqual({
			name: 'My cloud',
			enabled: false,
			priority: 5,
			scheduling_policy: 'round_robin',
			scheduling_max_consecutive_same_model: 2,
			engine: 'cloud',
			driver: 'cloud.fake',
			api_key: '',
			timeout_seconds: 1800
		});
	});

	it('leaves the new engine defaults alone when nothing was set', () => {
		const next = { name: '', enabled: true, driver: 'cloud.fake' };
		expect(carrySharedBackendFields({}, next)).toEqual(next);
	});
});
