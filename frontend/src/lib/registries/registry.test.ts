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

describe('createRegistry ownership layers', () => {
	it('unregistering one owner reveals the registration it shadowed', () => {
		const registry = createRegistry<string>('test');
		registry.register('k', 'core-value');
		registry.register('k', 'plugin-value', 'plugin:a');

		expect(registry.get('k')).toBe('plugin-value');

		registry.unregister('k', 'plugin:a');

		expect(registry.get('k')).toBe('core-value');
		expect(registry.has('k')).toBe(true);
	});

	it('unregistering an owner that is not on top leaves the visible value alone', () => {
		const registry = createRegistry<string>('test');
		registry.register('k', 'a-value', 'plugin:a');
		registry.register('k', 'b-value', 'plugin:b');

		registry.unregister('k', 'plugin:a');

		expect(registry.get('k')).toBe('b-value');
		expect(registry.keys()).toEqual(['k']);
	});

	it('re-registering an owner replaces its layer and moves it to the top', () => {
		const registry = createRegistry<string>('test');
		registry.register('k', 'a-1', 'plugin:a');
		registry.register('k', 'b-1', 'plugin:b');
		registry.register('k', 'a-2', 'plugin:a');

		expect(registry.get('k')).toBe('a-2');

		registry.unregister('k', 'plugin:a');

		expect(registry.get('k')).toBe('b-1');
	});

	it('unregistering without an owner drops every layer for the key', () => {
		const registry = createRegistry<string>('test');
		registry.register('k', 'core-value');
		registry.register('k', 'plugin-value', 'plugin:a');

		registry.unregister('k');

		expect(registry.has('k')).toBe(false);
	});

	it('unregistering an owner that never registered the key changes nothing', () => {
		const registry = createRegistry<string>('test');
		registry.register('k', 'core-value');

		registry.unregister('k', 'plugin:ghost');

		expect(registry.get('k')).toBe('core-value');
	});
});
