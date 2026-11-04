import { expect, test } from 'vitest';
import { QUICK_TAB, SETTINGS_TAB, activeTab, hasLocalSettings, panelTabs } from './panelTabs';
import type { NodeLike } from './types';

const preset = (extra: NodeLike = {}): NodeLike => ({ kind: 'preset', preset_id: 'p', mode: 'm', session_snapshot: { formData: {} }, ...extra });

test('a configured preset node gets Quick then All settings', () => {
	const tabs = panelTabs(preset(), true);
	expect(tabs.map((tab) => tab.id)).toStrictEqual([QUICK_TAB, SETTINGS_TAB]);
	expect(tabs[0].label).toBe('Quick');
	expect(tabs[1].label).toBe('All settings');
});

test('nodes with one section get no tabs', () => {
	expect(panelTabs({ kind: 'media' }, true)).toStrictEqual([]);
	expect(panelTabs({ kind: 'llm' }, true)).toStrictEqual([]);
	expect(panelTabs(preset(), false)).toStrictEqual([]);
	expect(panelTabs({ kind: 'preset' }, true)).toStrictEqual([]);
	expect(panelTabs(preset({ session_mode: 'copy', session_snapshot: null }), true)).toStrictEqual([]);
	expect(panelTabs(null, true)).toStrictEqual([]);
});

test('local form overrides mark the settings tab as edited', () => {
	const node = preset({ overrides: { formData: { steps: 12 } } });
	expect(hasLocalSettings(node)).toBe(true);
	expect(panelTabs(node, true)[1].label).toBe('All settings · Edited');
	expect(hasLocalSettings(preset({ overrides: { other: 1 } }))).toBe(false);
	expect(panelTabs(preset({ overrides: {} }), true)[1].label).toBe('All settings');
});

test('an unknown or stale tab falls back to Quick', () => {
	const tabs = panelTabs(preset(), true);
	expect(activeTab(tabs, SETTINGS_TAB)).toBe(SETTINGS_TAB);
	expect(activeTab(tabs, 'nope')).toBe(QUICK_TAB);
	expect(activeTab([], SETTINGS_TAB)).toBe(QUICK_TAB);
});

test('a node with its own settings gets the settings tab without a session', () => {
	const tabs = panelTabs({ kind: 'preset', preset_id: 'p', mode: 'm', session_mode: 'new', session_snapshot: null, overrides: {} }, true);
	expect(tabs.map((tab) => tab.id)).toStrictEqual([QUICK_TAB, SETTINGS_TAB]);
	expect(panelTabs({ kind: 'preset', preset_id: 'p', mode: 'm', session_mode: 'copy', session_snapshot: null }, true)).toStrictEqual([]);
});

test('a followed node shows its settings tab as read only', () => {
	const tabs = panelTabs(preset({ session_mode: 'follow', overrides: { formData: { steps: 3 } } }), true);
	expect(tabs[1].label).toBe('All settings');
});
