import { describe, expect, it, vi } from 'vitest';
import { Registry } from './registry';

interface Item {
	id: string;
	label: string;
}

describe('Registry', () => {
	it('registers, looks up and lists in insertion order', () => {
		const registry = new Registry<Item>();
		registry.register({ id: 'a', label: 'A' });
		registry.register({ id: 'b', label: 'B' });
		expect(registry.get('a')?.label).toBe('A');
		expect(registry.has('c')).toBe(false);
		expect(registry.list().map((i) => i.id)).toEqual(['a', 'b']);
	});

	it('returns an unregister that removes only its own registration', () => {
		const registry = new Registry<Item>();
		const off = registry.register({ id: 'a', label: 'first' });
		registry.register({ id: 'a', label: 'second' });
		off();
		expect(registry.get('a')?.label).toBe('second');
	});

	it('notifies subscribers on every change', () => {
		const registry = new Registry<Item>();
		const listener = vi.fn();
		const stop = registry.subscribe(listener);
		const off = registry.register({ id: 'a', label: 'A' });
		off();
		expect(listener).toHaveBeenCalledTimes(2);
		stop();
		registry.register({ id: 'b', label: 'B' });
		expect(listener).toHaveBeenCalledTimes(2);
	});
});
