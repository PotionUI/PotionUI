import { writable, type Readable } from 'svelte/store';
import { CORE_OWNER, createRegistry } from '$lib/registries/registry';
import { logger } from '$lib/utils/logger';
import { clearFailure, onGuardFailure } from './guard';
import { EDITOR_CONTRACT, type EditorEntryBase, type EditorKind } from './types';

export { CORE_OWNER, pluginOwner } from '$lib/registries/registry';

export interface EditorRegistry<T extends EditorEntryBase> {
	register(entry: T, owner?: string): () => void;
	get(editor: EditorKind, id: string): T | undefined;
	list(editor: EditorKind): T[];
	touch(): void;
	revision: Readable<number>;
}

const PLUGIN_PREFIX = 'plugin:';

const everything = writable(0);

export const editorsRevision: Readable<number> = { subscribe: everything.subscribe };

export function bumpEditorsRevision(): void {
	everything.update((tick) => tick + 1);
}

onGuardFailure(() => everything.update((tick) => tick + 1));

export function entryKey(editor: EditorKind, id: string): string {
	return `${editor}:${id}`;
}

export function pluginIdOfOwner(owner: string): string | null {
	return owner.startsWith(PLUGIN_PREFIX) ? owner.slice(PLUGIN_PREFIX.length) : null;
}

export function createEditorRegistry<T extends EditorEntryBase>(kind: string): EditorRegistry<T> {
	const layers = createRegistry<T>(kind);
	const live = new Map<string, T>();
	const own = writable(0);

	const bump = () => {
		own.update((tick) => tick + 1);
		everything.update((tick) => tick + 1);
	};

	const refuse = (entry: Partial<T>, reason: string) => {
		logger.warn(`Editor ${kind} registration refused for "${entry.id}": ${reason}`);
		return () => undefined;
	};

	return {
		register(entry, owner = CORE_OWNER) {
			if (!entry || typeof entry.id !== 'string' || !entry.id) return refuse(entry ?? {}, 'missing id');
			if (entry.editor !== 'image' && entry.editor !== 'video' && entry.editor !== 'audio') return refuse(entry, 'unknown editor');
			if ((entry.contract ?? 1) !== EDITOR_CONTRACT) return refuse(entry, `unsupported contract ${entry.contract}`);

			const key = entryKey(entry.editor, entry.id);
			const pluginId = pluginIdOfOwner(owner);
			if (pluginId) {
				if (!entry.id.startsWith(`${pluginId}.`)) return refuse(entry, `plugin ids start with "${pluginId}."`);
				const core = live.get(`${CORE_OWNER}\0${key}`);
				if (core && !core.overridable) return refuse(entry, 'a core entry already owns this id');
			}

			const stored = { ...entry } as T;
			if (pluginId) stored.pluginId = pluginId;
			else delete (stored as EditorEntryBase).pluginId;

			const liveKey = `${owner}\0${key}`;
			live.set(liveKey, stored);
			layers.register(key, stored, owner);
			clearFailure(key);
			bump();

			return () => {
				if (live.get(liveKey) !== stored) return;
				live.delete(liveKey);
				layers.unregister(key, owner);
				clearFailure(key);
				bump();
			};
		},
		get(editor, id) {
			return layers.get(entryKey(editor, id));
		},
		list(editor) {
			return layers.list().filter((entry) => entry.editor === editor);
		},
		touch: bump,
		revision: { subscribe: own.subscribe }
	};
}
