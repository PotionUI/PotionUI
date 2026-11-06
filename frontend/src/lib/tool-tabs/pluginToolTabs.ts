import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
import { pluginOwner } from '$lib/registries/registry';
import { registerToolKind } from './registry';
import type { PluginToolTabEntry, ToolKind, ToolKindDefinition } from './types';

export interface PluginToolTabDescriptor {
	id: string;
	register: () => void;
	dispose: () => void;
}

export function toolKindFromPluginEntry(entry: PluginToolTabEntry): ToolKindDefinition | null {
	if (!entry.plugin_id || !entry.tool || !entry.component || !entry.label) return null;
	return {
		tool: entry.tool as ToolKind,
		label: entry.label,
		icon: entry.icon || 'extension',
		accepts: entry.accepts ?? [],
		handles: entry.handles ?? [],
		pluginId: entry.plugin_id,
		load: () => resolvePluginComponent(entry.plugin_id, entry.component)
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
