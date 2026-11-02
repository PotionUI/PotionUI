import { describe, it, expect, vi } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn(async (pluginId: string, asset: string) => ({ pluginId, asset }))
}));

const { registerToolKind, toolKinds, coreToolKinds, pluginToolKinds, getToolKind, resolveToolComponent, toolLabel } = await import('./registry');
const { registerBuiltinToolKinds } = await import('./builtin');
const { describePluginToolTab, toolKindFromPluginEntry } = await import('./pluginToolTabs');
const { resolvePluginComponent } = await import('$lib/plugin-api/componentResolver');

const entry = {
	plugin_id: 'acme',
	id: 'stamp',
	tool: 'acme.stamp',
	label: 'Stamp tool',
	icon: 'brush',
	accepts: ['image'] as never,
	handles: ['image.crop'],
	component: 'StampToolTab.js'
};

describe('tool kind registry', () => {
	it('registers the core Image and Video editors first, once', () => {
		registerBuiltinToolKinds();
		registerBuiltinToolKinds();
		expect(get(coreToolKinds).map((kind) => [kind.tool, kind.label])).toEqual([
			['image', 'Image editor'],
			['video', 'Video editor']
		]);
		expect(toolLabel('video')).toBe('Video editor');
		expect(toolLabel('nope.tool')).toBe('nope.tool');
	});

	it('unregisters only the definition it registered', () => {
		const first = registerToolKind({ tool: 'demo.one', label: 'One', icon: 'brush', accepts: [], pluginId: 'demo' });
		const replacement = { tool: 'demo.one' as const, label: 'One again', icon: 'brush', accepts: [], pluginId: 'demo' };
		const second = registerToolKind(replacement);
		first();
		expect(getToolKind('demo.one')).toBe(replacement);
		second();
		expect(getToolKind('demo.one')).toBeUndefined();
	});

	it('resolves a component given directly or through a loader', async () => {
		const component = (() => {}) as never;
		expect(await resolveToolComponent({ tool: 'x.a', label: 'A', icon: 'x', accepts: [], component })).toBe(component);
		expect(await resolveToolComponent({ tool: 'x.b', label: 'B', icon: 'x', accepts: [], load: async () => ({ default: component }) })).toBe(component);
		expect(await resolveToolComponent({ tool: 'x.c', label: 'C', icon: 'x', accepts: [] })).toBeNull();
	});
});

describe('plugin tool tabs', () => {
	it('maps a served catalog entry to a kind as served', () => {
		expect(toolKindFromPluginEntry(entry)).toMatchObject({
			tool: 'acme.stamp',
			label: 'Stamp tool',
			icon: 'brush',
			accepts: ['image'],
			handles: ['image.crop'],
			pluginId: 'acme'
		});
	});

	it('skips an entry with no tool or no component', () => {
		expect(toolKindFromPluginEntry({ ...entry, tool: '' })).toBeNull();
		expect(toolKindFromPluginEntry({ ...entry, component: '' })).toBeNull();
	});

	it('loads the served component from the plugin dist', async () => {
		const definition = toolKindFromPluginEntry(entry)!;
		await resolveToolComponent(definition);
		expect(resolvePluginComponent).toHaveBeenCalledWith('acme', 'StampToolTab.js');
	});

	it('registers under "From plugins" and disposes cleanly, keeping one definition per identity', () => {
		const descriptor = describePluginToolTab(entry, 'rev-1')!;
		descriptor.register();
		const registered = getToolKind('acme.stamp');
		expect(get(pluginToolKinds).map((kind) => kind.tool)).toContain('acme.stamp');
		expect(get(toolKinds).at(-1)?.tool).toBe('acme.stamp');

		const same = describePluginToolTab(entry, 'rev-1')!;
		expect(same.id).toBe(descriptor.id);
		same.register();
		expect(getToolKind('acme.stamp')).toBe(registered);

		expect(describePluginToolTab(entry, 'rev-2')!.id).not.toBe(descriptor.id);

		same.dispose();
		expect(getToolKind('acme.stamp')).toBeUndefined();
	});
});
