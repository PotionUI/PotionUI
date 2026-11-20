import { get } from 'svelte/store';
import { describe, expect, it } from 'vitest';
import { railTools } from '$lib/editors/railTools';
import { ensureCoreImageEditor } from '$lib/tool-tabs/image/coreEditor';
import { settingsOf } from '$lib/tool-tabs/image/generate/register';
import { tabsStore } from '$lib/stores/tabs';
import { detachedFormTab, localSectionCollapsed } from './detachedFormTab';

describe('detachedFormTab', () => {
	it('is a generation tab for one preset mode that never enters the tabs store', () => {
		const before = get(tabsStore).tabs.length;
		const tab = detachedFormTab({ id: 'editor-settings:x', presetId: 'qwen', mode: 'editor:edit', formData: { steps: 20 } });
		expect(tab).toMatchObject({ kind: 'generation', id: 'editor-settings:x', selectedPreset: 'qwen', selectedMode: 'editor:edit', formData: { steps: 20 } });
		expect(get(tabsStore).tabs).toHaveLength(before);
	});
});

describe('localSectionCollapsed', () => {
	it('remembers a fold per preset and mode without touching any tab', () => {
		const here = localSectionCollapsed('qwen', 'edit');
		here.set('custom/image', true);
		expect(get(here.folded)).toEqual({ 'custom/image': true });
		expect(get(localSectionCollapsed('qwen', 'other').folded)).toEqual({});
		here.set('custom/image', false);
		expect(get(localSectionCollapsed('qwen', 'edit').folded)).toEqual({ 'custom/image': false });
	});
});

describe('settingsOf', () => {
	it('gives every task tool the settings view and a plain tool none', () => {
		ensureCoreImageEditor();
		expect(settingsOf(railTools.get('image', 'edit_region'))).toBeTruthy();
		expect(settingsOf({ id: 'acme.relight', editor: 'image', group: 'generate', label: 'Relight', icon: 'sparkles', task: 'acme.relight' })).toBeTruthy();
		expect(settingsOf({ id: 'paint', editor: 'image', group: 'edit', label: 'Paint', icon: 'pencil' })).toBeUndefined();
		expect(settingsOf(undefined)).toBeUndefined();
	});
});
