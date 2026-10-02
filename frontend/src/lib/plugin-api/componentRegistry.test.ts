import { describe, it, expect, vi, afterEach } from 'vitest';
import { getRegistry, registerLazyComponent } from './componentRegistry';

describe('registerLazyComponent after a failed load', () => {
	afterEach(() => {
		vi.restoreAllMocks();
	});

	async function mountFailing(name: string) {
		const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
		const load = vi.fn(() => Promise.reject(new Error('chunk failed')));
		registerLazyComponent(name, load);
		const entry = getRegistry()[name];
		const handle = entry.mount({} as HTMLElement, { label: 'one' });
		await vi.waitFor(() => expect(errors).toHaveBeenCalled());
		return { entry, handle, load, errors };
	}

	it('accepts updates and unmount on the handle without throwing or loading again', async () => {
		const { entry, handle, load, errors } = await mountFailing('FailingUpdate');

		expect(() => entry.update(handle, { label: 'two' })).not.toThrow();
		expect(() => entry.unmount(handle)).not.toThrow();
		expect(() => entry.unmount(handle)).not.toThrow();

		expect(load).toHaveBeenCalledTimes(1);
		expect(String(errors.mock.calls[0][0])).toContain('FailingUpdate');
	});

	it('ignores update and unmount for a missing handle', () => {
		registerLazyComponent('MissingHandle', () => Promise.reject(new Error('never mounted')));
		const entry = getRegistry().MissingHandle;

		expect(() => entry.update(undefined, { label: 'x' })).not.toThrow();
		expect(() => entry.unmount(undefined)).not.toThrow();
	});
});
