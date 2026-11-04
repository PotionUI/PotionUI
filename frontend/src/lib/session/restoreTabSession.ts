import { get } from 'svelte/store';
import { api } from '$lib/services/api';
import { tabsStore } from '$lib/stores/tabs';
import { tabDraftSync } from '$lib/stores/tabDraftSync';
import { toasts } from '$lib/stores/toast';
import { findGenerationTab, type GenerationTab } from '$lib/types/tabs';
import { buildSessionRestoreTabPatch } from '$lib/utils/sessionRestore';
import { seedModeStateFromSessionData } from '$lib/utils/modeState';
import {
	collectTabSessionData,
	isSessionGoneError,
	isSessionMissingResponse,
	shouldRestoreTabSessionOnMount
} from '$lib/utils/sessionTabState';
import { decideSessionVariables, finishDeferredSessionVariables } from '$lib/session/sessionVariablesRestore';

export interface RestoreTabSessionContext {
	presets: Array<{ id: string; version?: string }>;
}

function clearLink(tabId: string) {
	tabsStore.updateTab(tabId, {
		selectedSessionId: null,
		savedSessionSignature: null,
		sessionBaselineAwaitingFormNormalization: false
	});
}

export async function restoreTabSession(tab: GenerationTab, context: RestoreTabSessionContext): Promise<void> {
	if (!shouldRestoreTabSessionOnMount(tab)) return;
	const selectedSessionId = tab.selectedSessionId!;
	const selectedMode = tab.selectedMode!;

	try {
		const response = await api.getSessionById(selectedSessionId);
		if (response.success && response.data) {
			const session = response.data;
			const modeData = session.data[selectedMode];

			if (modeData) {
				const presetForSession = context.presets.find((p) => p.id === (modeData.selectedPreset || tab.selectedPreset));
				if (modeData.presetVersion && presetForSession?.version && modeData.presetVersion !== presetForSession.version) {
					toasts.warning(
						`Session "${tab.name}" was saved with preset version ${modeData.presetVersion}, now at ${presetForSession.version} — some fields may have changed.`
					);
				}

				const restoredPatch = buildSessionRestoreTabPatch(modeData, {
					promptPanelWidth: tab.promptPanelWidth
				});
				const draftReady = await tabDraftSync.settled(4000);
				const currentVariables = () => findGenerationTab(get(tabsStore).tabs, tab.id)?.variables;
				const variablesDecision = decideSessionVariables(draftReady, currentVariables());
				const { variables: sessionVariables, ...patchWithoutVariables } = restoredPatch;
				const appliedPatch = variablesDecision === 'replace' ? restoredPatch : patchWithoutVariables;
				const restoredTab = {
					...tab,
					selectedPreset: modeData.selectedPreset || tab.selectedPreset,
					selectedVariant: modeData.selectedVariant || null,
					selectedSessionId: tab.selectedSessionId,
					...restoredPatch,
					modeStateByMode: seedModeStateFromSessionData(session.data, tab.selectedMode)
				};

				tabsStore.updateTab(tab.id, {
					selectedPreset: modeData.selectedPreset || tab.selectedPreset,
					selectedVariant: modeData.selectedVariant || null,
					selectedSessionId: tab.selectedSessionId,
					...appliedPatch,
					modeStateByMode: restoredTab.modeStateByMode,
					sessionVersion: session.version,
					savedSessionSignature: JSON.stringify(
						collectTabSessionData(restoredTab, tab.selectedMode, session.data, presetForSession?.version)
					),
					sessionBaselineAwaitingFormNormalization: true
				});
				if (variablesDecision === 'replace') tabDraftSync.replaceDraft(tab.id, sessionVariables ?? {});
				if (variablesDecision === 'defer') {
					void finishDeferredSessionVariables(
						{
							settled: () => tabDraftSync.settled(),
							currentVariables,
							apply: (variables) => tabsStore.updateTab(tab.id, { variables }),
							replace: (variables) => tabDraftSync.replaceDraft(tab.id, variables)
						},
						sessionVariables ?? {}
					);
				}
			}
		} else if (isSessionMissingResponse(response)) {
			console.warn(`[TabRestore] Session for tab ${tab.name} no longer exists, clearing the link.`);
			clearLink(tab.id);
		}
	} catch (error) {
		if (isSessionGoneError(error)) {
			console.error(`[TabRestore] Failed to restore session for tab ${tab.name}:`, error);
			clearLink(tab.id);
		} else {
			console.warn(`[TabRestore] Backend unreachable while restoring session for tab ${tab.name}, keeping the saved link:`, error);
		}
	}
}
