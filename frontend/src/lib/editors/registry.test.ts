import { get } from 'svelte/store';
import { describe, expect, it } from 'vitest';
import { CORE_OWNER, createEditorRegistry, editorsRevision, entryKey, pluginOwner } from './registry';
import type { EditorEntryBase } from './types';

interface Probe extends EditorEntryBase {
	label: string;
}

const probe = (id: string, label = id, editor: Probe['editor'] = 'image'): Probe => ({ id, editor, label });

describe('createEditorRegistry', () => {
	it('registers, looks up by editor and id, and unregisters', () => {
		const registry = createEditorRegistry<Probe>('probe');
		const dispose = registry.register(probe('crop'));
		expect(registry.get('image', 'crop')?.label).toBe('crop');
		expect(registry.get('video', 'crop')).toBeUndefined();
		expect(registry.list('image').map((entry) => entry.id)).toEqual(['crop']);
		dispose();
		expect(registry.get('image', 'crop')).toBeUndefined();
		expect(registry.list('image')).toEqual([]);
	});

	it('admits video and audio entries without any change', () => {
		const registry = createEditorRegistry<Probe>('probe');
		registry.register(probe('trim', 'trim', 'video'));
		registry.register(probe('fade', 'fade', 'audio'));
		expect(registry.list('video').map((entry) => entry.id)).toEqual(['trim']);
		expect(registry.list('audio').map((entry) => entry.id)).toEqual(['fade']);
		expect(registry.list('image')).toEqual([]);
	});

	it('bumps the revision on register and unregister, and the shared revision too', () => {
		const registry = createEditorRegistry<Probe>('probe');
		const start = get(registry.revision);
		const sharedStart = get(editorsRevision);
		const dispose = registry.register(probe('crop'));
		expect(get(registry.revision)).toBe(start + 1);
		dispose();
		expect(get(registry.revision)).toBe(start + 2);
		expect(get(editorsRevision)).toBe(sharedStart + 2);
	});

	it('does not bump when a disposer runs twice or after a newer registration took its place', () => {
		const registry = createEditorRegistry<Probe>('probe');
		const first = registry.register(probe('crop', 'one'));
		const second = registry.register(probe('crop', 'two'));
		const before = get(registry.revision);
		first();
		expect(get(registry.revision)).toBe(before);
		expect(registry.get('image', 'crop')?.label).toBe('two');
		second();
		const after = get(registry.revision);
		second();
		expect(get(registry.revision)).toBe(after);
		expect(registry.get('image', 'crop')).toBeUndefined();
	});

	it('exposes touch to notify views without changing entries', () => {
		const registry = createEditorRegistry<Probe>('probe');
		const before = get(registry.revision);
		registry.touch();
		expect(get(registry.revision)).toBe(before + 1);
	});

	it('refuses an unknown contract major, a missing id and an unknown editor', () => {
		const registry = createEditorRegistry<Probe>('probe');
		registry.register({ ...probe('crop'), contract: 2 });
		registry.register({ ...probe(''), id: '' });
		registry.register({ ...probe('x'), editor: 'sound' as never });
		expect(registry.list('image')).toEqual([]);
		registry.register({ ...probe('crop'), contract: 1 });
		expect(registry.list('image')).toHaveLength(1);
	});

	it('requires plugin ids to start with the plugin id and accepts core ids unprefixed', () => {
		const registry = createEditorRegistry<Probe>('probe');
		registry.register(probe('crop'));
		registry.register(probe('halftone'), pluginOwner('acme'));
		registry.register(probe('other.halftone'), pluginOwner('acme'));
		expect(registry.get('image', 'halftone')).toBeUndefined();
		expect(registry.get('image', 'other.halftone')).toBeUndefined();
		const dispose = registry.register(probe('acme.halftone'), pluginOwner('acme'));
		expect(registry.get('image', 'acme.halftone')?.pluginId).toBe('acme');
		expect(registry.get('image', 'crop')?.pluginId).toBeUndefined();
		dispose();
		expect(registry.get('image', 'acme.halftone')).toBeUndefined();
	});

	it('refuses a plugin entry that collides with a core id unless the core entry is overridable', () => {
		const registry = createEditorRegistry<Probe>('probe');
		registry.register(probe('mask.brush', 'core'));
		registry.register(probe('mask.brush', 'plugin'), pluginOwner('mask'));
		expect(registry.get('image', 'mask.brush')?.label).toBe('core');

		registry.register({ ...probe('acme.sharpen', 'core'), overridable: true });
		const dispose = registry.register(probe('acme.sharpen', 'plugin'), pluginOwner('acme'));
		expect(registry.get('image', 'acme.sharpen')?.label).toBe('plugin');
		dispose();
		expect(registry.get('image', 'acme.sharpen')?.label).toBe('core');
	});

	it('lets the same owner register an id again and the last registration wins', () => {
		const registry = createEditorRegistry<Probe>('probe');
		registry.register(probe('acme.a', 'first'), pluginOwner('acme'));
		registry.register(probe('acme.a', 'second'), pluginOwner('acme'));
		expect(registry.get('image', 'acme.a')?.label).toBe('second');
		expect(registry.list('image')).toHaveLength(1);
	});

	it('never lets an entry choose its own pluginId', () => {
		const registry = createEditorRegistry<Probe>('probe');
		registry.register({ ...probe('crop'), pluginId: 'forged' });
		registry.register({ ...probe('acme.a'), pluginId: 'forged' }, pluginOwner('acme'));
		expect(registry.get('image', 'crop')?.pluginId).toBeUndefined();
		expect(registry.get('image', 'acme.a')?.pluginId).toBe('acme');
	});

	it('keys entries by editor and id', () => {
		expect(entryKey('image', 'crop')).toBe('image:crop');
		expect(CORE_OWNER).toBe('core');
	});
});
