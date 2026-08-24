// Global defaults for the per-tab generation-sound toggles (`Tab.soundOnComplete`
// / `Tab.soundOnError`) and the "apply to all tabs" preference that governs
// whether changing a toggle writes these defaults and propagates to every
// open tab. Read by `tabsStore` (seeding a freshly created tab) and written by
// the generation settings drawer.
import { storage } from './storage';

export type SoundKind = 'complete' | 'error';

const DEFAULT_KEYS: Record<SoundKind, string> = {
	complete: 'potionui_sound_on_complete_default',
	error: 'potionui_sound_on_error_default'
};

const APPLY_TO_ALL_TABS_KEY = 'potionui_sound_apply_to_all_tabs';

export function getGlobalSoundDefault(kind: SoundKind): boolean {
	const raw = storage.get(DEFAULT_KEYS[kind]);
	return raw === null ? true : raw === 'true';
}

export function setGlobalSoundDefault(kind: SoundKind, value: boolean): void {
	storage.set(DEFAULT_KEYS[kind], String(value));
}

export function getApplySoundToAllTabs(): boolean {
	return storage.get(APPLY_TO_ALL_TABS_KEY) === 'true';
}

export function setApplySoundToAllTabs(value: boolean): void {
	storage.set(APPLY_TO_ALL_TABS_KEY, String(value));
}
