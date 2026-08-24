import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createRegistry } from './registry';

describe('createRegistry', () => {
	it('registers and retrieves a value by key', () => {
		const registry = createRegistry<{ label: string }>('test');
		registry.register('a', { label: 'A' });

		expect(registry.get('a')).toEqual({ label: 'A' });
		expect(registry.has('a')).toBe(true);
		expect(registry.has('missing')).toBe(false);
	});

	it('returns undefined for an unregistered key', () => {
		const registry = createRegistry<string>('test');
		expect(registry.get('nope')).toBeUndefined();
	});

	it('list() returns all registered values', () => {
		const registry = createRegistry<number>('test');
		registry.register('a', 1);
		registry.register('b', 2);

		expect(registry.list().sort()).toEqual([1, 2]);
	});

	it('keys() returns all registered keys', () => {
		const registry = createRegistry<number>('test');
		registry.register('a', 1);
		registry.register('b', 2);

		expect(registry.keys().sort()).toEqual(['a', 'b']);
	});

	it('unregister removes a key', () => {
		const registry = createRegistry<number>('test');
		registry.register('a', 1);
		registry.unregister('a');

		expect(registry.get('a')).toBeUndefined();
		expect(registry.has('a')).toBe(false);
	});

	it('last-wins: re-registering a key overrides the previous value', () => {
		const registry = createRegistry<number>('test');
		registry.register('a', 1);
		registry.register('a', 2);

		expect(registry.get('a')).toBe(2);
		expect(registry.list()).toEqual([2]);
	});

	it('warns on override only in dev mode', () => {
		const registry = createRegistry<number>('test');
		const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

		registry.register('a', 1);
		registry.register('a', 2);

		if (import.meta.env.DEV) {
			expect(warnSpy).toHaveBeenCalledTimes(1);
			expect(warnSpy.mock.calls[0][0]).toContain('test');
		} else {
			expect(warnSpy).not.toHaveBeenCalled();
		}

		warnSpy.mockRestore();
	});

	it('does not warn on first registration', () => {
		const registry = createRegistry<number>('test');
		const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

		registry.register('a', 1);

		expect(warnSpy).not.toHaveBeenCalled();
		warnSpy.mockRestore();
	});
});
