import { describe, it, expect } from 'vitest';
import { buildSessionRestoreTabPatch } from './sessionRestore';
import type { SessionData } from '$lib/types/api';
import { createTextVariable, createChoiceVariable } from './variableDefs';

describe('buildSessionRestoreTabPatch', () => {
	it('carries variables through — the field that went missing from the auto-restore-on-mount path', () => {
		const modeData: SessionData = {
			prompt: 'a cat',
			variables: { mood: createTextVariable('noir'), palette: createChoiceVariable(['warm', 'cool']) }
		};
		const patch = buildSessionRestoreTabPatch(modeData);
		expect(patch.variables).toEqual({
			mood: createTextVariable('noir'),
			palette: createChoiceVariable(['warm', 'cool'])
		});
	});

	it('defaults variables to an empty map when the saved session has none', () => {
		const patch = buildSessionRestoreTabPatch({ prompt: 'a cat' });
		expect(patch.variables).toEqual({});
	});

	it('restores segments from the current key names', () => {
		const modeData: SessionData = {
			promptSegments: [{ id: 's1', content: 'a cat' }] as any,
			negativePromptSegments: [{ id: 'n1', content: 'blurry' }] as any
		};
		const patch = buildSessionRestoreTabPatch(modeData);
		expect(patch.promptSegments).toEqual([{ id: 's1', content: 'a cat' }]);
		expect(patch.negativePromptSegments).toEqual([{ id: 'n1', content: 'blurry' }]);
	});

	it('leaves promptSegments/negativePromptSegments undefined (not []) when the session has neither key, to avoid a phantom "unsaved changes" diff', () => {
		const patch = buildSessionRestoreTabPatch({ prompt: '' });
		expect(patch.promptSegments).toBeUndefined();
		expect(patch.negativePromptSegments).toBeUndefined();
	});

	it('falls back to the pre-rename legacy segment keys when present', () => {
		const modeData = {
			segments: [{ id: 's1', content: 'legacy positive' }],
			negativeSegments: [{ id: 'n1', content: 'legacy negative' }]
		} as SessionData;
		const patch = buildSessionRestoreTabPatch(modeData);
		expect(patch.promptSegments).toEqual([{ id: 's1', content: 'legacy positive' }]);
		expect(patch.negativePromptSegments).toEqual([{ id: 'n1', content: 'legacy negative' }]);
	});

	it('restores multi-prompt and video-director/prompt-relay state', () => {
		const modeData: SessionData = {
			promptTabs: [{ prompt: 'a', negativePrompt: '', promptSegments: [], negativePromptSegments: [] }],
			activePromptTab: 1
		};
		const patch = buildSessionRestoreTabPatch(modeData);
		expect(patch.promptTabs).toHaveLength(1);
		expect(patch.activePromptTab).toBe(1);
	});

	it('only includes leftPanelCollapsed when the saved session actually set it (avoids a phantom diff)', () => {
		expect(buildSessionRestoreTabPatch({ prompt: '' })).not.toHaveProperty('leftPanelCollapsed');
		expect(buildSessionRestoreTabPatch({ prompt: '', leftPanelCollapsed: true }).leftPanelCollapsed).toBe(true);
		expect(buildSessionRestoreTabPatch({ prompt: '', leftPanelCollapsed: false }).leftPanelCollapsed).toBe(false);
	});

	it('only includes layoutMode when it is a recognized value', () => {
		expect(buildSessionRestoreTabPatch({ prompt: '' })).not.toHaveProperty('layoutMode');
		expect(buildSessionRestoreTabPatch({ prompt: '', layoutMode: 'three' }).layoutMode).toBe('three');
	});

	it('falls back to the caller-provided backend id and panel width when the session has none', () => {
		const patch = buildSessionRestoreTabPatch({ prompt: '' }, { selectedBackendId: 'backend-1', promptPanelWidth: 500 });
		expect(patch.selectedBackendId).toBe('backend-1');
		expect(patch.promptPanelWidth).toBe(500);
	});

	it('prefers the session-saved backend id and panel width over the fallback', () => {
		const modeData: SessionData = { prompt: '', selectedBackendId: 'backend-2', promptPanelWidth: 600 };
		const patch = buildSessionRestoreTabPatch(modeData, { selectedBackendId: 'backend-1', promptPanelWidth: 500 });
		expect(patch.selectedBackendId).toBe('backend-2');
		expect(patch.promptPanelWidth).toBe(600);
	});

	it('restores workbench and left-panel sizing with sensible defaults', () => {
		expect(buildSessionRestoreTabPatch({ prompt: '' }).workbenchMaxHeight).toBe('600');
		expect(buildSessionRestoreTabPatch({ prompt: '' }).leftPanelWidth).toBe(380);
		expect(buildSessionRestoreTabPatch({ prompt: '', workbenchMaxHeight: '800', leftPanelWidth: 420 })).toMatchObject({
			workbenchMaxHeight: '800',
			leftPanelWidth: 420
		});
	});

	it('ignores a legacy flowAppearance field from a session saved before Flow view was removed', () => {
		const modeData = { prompt: '', flowAppearance: { lineHeight: 2.8, underline: 'strong' } } as SessionData;
		const patch = buildSessionRestoreTabPatch(modeData);
		expect(patch).not.toHaveProperty('flowAppearance');
	});
});
