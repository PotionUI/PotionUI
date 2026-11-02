import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
import { pluginOwner } from '$lib/registries/registry';
import { registerToolKind } from './registry';
import type { PluginToolTabEntry, ToolKind, ToolKindDefinition, ToolMediaKind } from './types';

const MEDIA_KINDS: ReadonlySet<string> = new Set(['image', 'video', 'audio']);

export interface PluginToolTabDescriptor {
	id: string;
	register: () => void;
	dispose: () => void;
}

export function pluginToolKindId(entry: Pick<PluginToolTabEntry, 'plugin_id' | 'id' | 'tool'>): ToolKind | null {
	if (!entry.plugin_id || !entry.id) return null;
	const expected = `${entry.plugin_id}.${entry.id}`;
	if (entry.tool && entry.tool !== expected) return null;
	return expected as ToolKind;
}

export function toolKindFromPluginEntry(entry: PluginToolTabEntry): ToolKindDefinition | null {
	const tool = pluginToolKindId(entry);
	if (!tool || !entry.component || !entry.label) return null;
	const asset = entry.component.replace(/^frontend\/dist\//, '');
	return {
		tool,
		label: entry.label,
		icon: entry.icon || 'extension',
		accepts: (entry.accepts ?? []).filter((kind): kind is ToolMediaKind => MEDIA_KINDS.has(kind)),
		handles: entry.handles ?? [],
		pluginId: entry.plugin_id,
		load: () => resolvePluginComponent(entry.plugin_id, asset)
	};
}

const definitions = new Map<string, ToolKindDefinition>();

export function describePluginToolTab(entry: PluginToolTabEntry, revision: string): PluginToolTabDescriptor | null {
	const fresh = toolKindFromPluginEntry(entry);
	if (!fresh) return null;
	const id = `tool_tab|${pluginOwner(entry.plugin_id)}|${fresh.tool}|${entry.component}|${fresh.label}|${fresh.icon}|${fresh.accepts.join(',')}|${(fresh.handles ?? []).join(',')}|${revision}`;
	const definition = definitions.get(id) ?? fresh;
	definitions.set(id, definition);
	let dispose: (() => void) | null = null;
	return {
		id,
		register: () => {
			dispose?.();
			dispose = registerToolKind(definition);
		},
		dispose: () => {
			dispose?.();
			dispose = null;
		}
	};
}
