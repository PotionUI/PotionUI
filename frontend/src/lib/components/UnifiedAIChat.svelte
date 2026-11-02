<script lang="ts">
	import { refusalMessage } from '$lib/plans/refusal';
	import { blockedUploadMessage } from '$lib/plans/uploadLimit';
	import { getErrorMessage, logger } from '$lib/utils/logger';
	import { onDestroy, tick } from 'svelte';
	import { browser } from '$app/environment';
	import { scopedStorage } from '$lib/stores/identityScopedStorage';
	import { api, type ChatSessionResponse } from '$lib/services/api/index';
	import { loadPresets } from '$lib/stores/presetsCatalog';
	import { canApplySegmentAction } from '$lib/chat/conditionText';
	import ChatMessage from '$lib/components/ChatMessage.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Logo from '$lib/components/brand/Logo.svelte';
	import { buildUploadedMediaItem } from '$lib/components/form-fields/mediaLoaderUpload';
	import { activeTab, activeToolTab, tabsStore } from '$lib/stores/tabs';
	import { generationTabs } from '$lib/types/tabs';
	import { loraSelectionsForTab } from '$lib/stores/loraPickerSelections';
	import { page } from '$app/stores';
	import { chatSession } from '$lib/stores/chatSession';
	import { chatComposerDrafts } from '$lib/stores/chatComposerDrafts';
	import { chatModes, resolveModeForRoute, resolveModeName, toolsForMode } from '$lib/stores/chatModes';
	import { declaredMode, collectProvidedContext, dispatchToolApplied } from '$lib/chat/pageContext';
	import {
		chatFormSessionId,
		isGeneratePageContext,
		isGenerationPresetContext,
		resolveChatContextTab
	} from '$lib/chat/activeFormContext';
	import ChatHeader from '$lib/components/chat/ChatHeader.svelte';
	import ChatMemoryPanel from '$lib/components/chat/ChatMemoryPanel.svelte';
	import ChatToolPreferencesPanel from '$lib/components/chat/ChatToolPreferencesPanel.svelte';
	import type { UserToolPreference } from '$lib/types/llm';
	import ChatHistoryView from '$lib/components/chat/ChatHistoryView.svelte';
	import { authStore } from '$lib/stores/auth';
	import ChatInput from '$lib/components/chat/ChatInput.svelte';
	import QuestionDock from '$lib/components/chat/QuestionDock.svelte';
	import ChatContextStrip from '$lib/components/chat/ChatContextStrip.svelte';
	import { deriveContextStripModel, deriveTabSwitchDivider } from '$lib/chat/contextStrip';
	import ChatThinkingBubble from '$lib/components/chat/ChatThinkingBubble.svelte';
	import {
		actionToolLabel,
		continuationErrorText,
		latestPendingExecution,
		markExecutionRejected,
		redoRequestText,
		replaceExecution,
		type ActionDecision,
		type ActionResolveOutcome
	} from '$lib/chat/actionCard';
	import { deriveQuestionQueue, dismissedQuestions } from '$lib/chat/questionQueue';
	import type {
		UnifiedChatMessageData as ChatMessageData,
		ResourceChipData,
		ToolExecution
	} from '$lib/types/chat';
	import type { Segment } from '$lib/types/segments';
	import { buildChatFormState } from '$lib/chat/chatFormState';
	import { flattenRichSegments, isSegmentEnabled } from '$lib/utils/richSegments';
	import {
		applySegmentAdd,
		applySegmentDelete,
		applySegmentUpdate,
		locateSegmentInLists,
		restoreSegment,
		type SegmentListKey,
		type SegmentLists
	} from '$lib/utils/promptSegments';
	import { lastAppliedSegment } from '$lib/stores/lastAppliedSegment';
	import { appliedSegmentActions } from '$lib/stores/appliedSegmentActions';
	import { removedSegmentActions, removedActionKey } from '$lib/stores/removedSegmentActions';
	import { promptActionUndos, promptActionMarkerKey } from '$lib/stores/promptActionUndos';
	import { toasts } from '$lib/stores/toast';
	import {
		applyPromptListAction,
		applySegmentActionToPrompt,
		restoreSegmentInPrompt,
		segmentTextInPrompt,
		undoPromptListAction
	} from '$lib/chat/promptActions';
	import { isPromptListAction } from '$lib/utils/markdown';
	import { activePromptIndex, promptCount, promptList } from '$lib/generation/promptBatch';
	import { applyTitle, mapPersistedMessage } from '$lib/utils/chatStream';
	import {
		sendMessage as turnControllerSendMessage,
		reattachToTurn as turnControllerReattachToTurn,
		type TurnControllerDeps,
		type SendMessagePayload
	} from '$lib/chat/turnController';
	import { scheduleFrame, cancelFrame } from '$lib/chat/streamCoalescer';
	import {
		parseDirectorCapabilities,
		resolveDirectorRaw,
		normalizeDirectorValue,
		applyDirectorSegmentPrompt,
		resolveDirectorSegmentTarget
	} from '$lib/utils/videoDirector';
	import { applyModelOverlay, directorOverlayFrom, selectedCloudModelId } from '$lib/utils/cloudDirector';
	import { sharedCapabilityCache } from '$lib/form/capabilityTracker';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import {
		resolveMusicDirectorCapabilities,
		normalizeMusicDirectorValue,
		applyMusicDirectorOperations
	} from '$lib/utils/musicDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
	import { collectFormImages, type FormImageEntry } from '$lib/chat/formMedia';
	import {
		loadFromStorage,
		saveToStorage,
		loadConfigurations,
		resolveConfigId,
		loadActiveSessionId,
		saveActiveSessionId,
		loadDisabledTools,
		saveDisabledTools
	} from '$lib/utils/chatConfig';
	import { introForMode, startersForMode } from '$lib/chat/modeStarters';
	import { loadHistoryRailCollapsed, saveHistoryRailCollapsed } from '$lib/utils/chatHistoryRail';
	import { getPresetPromptResources, type PresetPromptResourcesResult } from '$lib/utils/presetPromptResourcesCache';
	import type { ResourceTokenContext } from '$lib/utils/promptResourceTokens';
	import Icon from '$lib/components/Icon.svelte';

	// Props
	export let onClose: (() => void) | undefined = undefined;

	// LocalStorage keys (the active session id is a single global key, see chatConfig.ts)
	const STORAGE_KEY_CONFIG = 'unified-ai-chat-config-id';
	const STORAGE_KEY_ATTACH_IMAGE = 'unified-ai-chat-attach-image';
	const STORAGE_KEY_PINNED_TAB = 'unified-ai-chat-pinned-tab';
	const STORAGE_KEY_ENABLE_TOOLS = 'unified-ai-chat-enable-tools';

	// Loading a session opens only its last TRANSCRIPT_TAIL messages (windowed
	// transcript); loadEarlier() pages further back in the same size steps.
	const TRANSCRIPT_TAIL = 60;

	// Conversation state lives in the chatSession store (survives panel close)
	$: sessionId = $chatSession.sessionId;
	$: messages = $chatSession.messages;
	$: currentMode = $chatSession.mode;
	$: starters = startersForMode(currentMode);
	$: intro = introForMode(currentMode);
	$: isGenerating = $chatSession.isGenerating;
	// What a brand-new conversation would resolve to right now — used only to
	// tell the user a locked conversation's mode has fallen behind the page
	// (see ChatModeSelector's scope-mismatch notice); doesn't itself change
	// `currentMode` (see loadAllData, which resolves it once for a conversation
	// that's still fresh).
	$: currentPageMode = $declaredMode || resolveModeForRoute($page.url.pathname, $chatModes.modes);
	$: pendingAction = latestPendingExecution(messages);
	$: pendingQuestionQueue = deriveQuestionQueue(messages, $dismissedQuestions);

	// UI state
	let userInput = '';
	let userResources: Record<string, ResourceChipData> = {};

	// The composer's local state above doesn't survive GlobalChatPanel
	// unmounting this component on close - restore/persist it against
	// chatComposerDrafts (module-scope, keyed by session id) so a draft typed
	// before closing the drawer is still there on reopen. Restoring keys off
	// `sessionId` rather than running once on mount also covers switching to a
	// different session (loadSession) and starting a new one (sessionId ->
	// null): each transition adopts that session's own draft.
	let lastDraftSessionId: string | null | undefined = undefined;
	$: if (sessionId !== lastDraftSessionId) {
		lastDraftSessionId = sessionId;
		const draft = chatComposerDrafts.load(sessionId);
		userInput = draft?.text ?? '';
		userResources = draft?.resources ?? {};
	}
	$: chatComposerDrafts.save(sessionId, { text: userInput, resources: userResources });

	let showMemoryPanel = false;
	let showToolPreferencesPanel = false;

	// History rail (left of the conversation, not a covering view) — persists
	// only the collapsed/expanded boolean, read once on mount. Exported so the
	// host (GlobalChatPanel) can bind it onto .chat-shell itself: the CSS rule
	// that actually collapses/widens things (&.rail-collapsed …) targets that
	// ancestor, not .history-rail.
	export let historyRailCollapsed = loadHistoryRailCollapsed();
	function toggleHistoryRail() {
		historyRailCollapsed = !historyRailCollapsed;
		saveHistoryRailCollapsed(historyRailCollapsed);
	}

	// Opening Memory collapses the rail (screen real estate goes to the
	// inspector instead) and restores whatever the rail's state was before —
	// this collapse is transient UI, not a user preference, so it bypasses
	// saveHistoryRailCollapsed entirely (mirrors the mock's openMemory/closeMemory).
	let railCollapsedBeforeMemory: boolean | null = null;
	function openMemoryPanel() {
		if (!showMemoryPanel) {
			railCollapsedBeforeMemory = historyRailCollapsed;
			historyRailCollapsed = true;
		}
		showMemoryPanel = true;
	}
	function closeMemoryPanel() {
		showMemoryPanel = false;
		if (railCollapsedBeforeMemory !== null) {
			historyRailCollapsed = railCollapsedBeforeMemory;
			railCollapsedBeforeMemory = null;
		}
	}
	// null = not yet known, hides the composer's "Memory · n" badge (see
	// ChatInput). While the memory panel is mounted, its exact per-scope count
	// (global + preset + resolved model) drives this via onCountChange below;
	// at rest, loadMemoryNoteCountAtRest() below gives a cheaper global+preset
	// approximation so the badge isn't empty before the panel is ever opened.
	let memoryNoteCount: number | null = null;
	let memoryNoteCountPresetCache: string | null = null;
	async function loadMemoryNoteCountAtRest(presetId: string | null) {
		if (memoryNoteCountPresetCache === (presetId ?? '')) return;
		memoryNoteCountPresetCache = presetId ?? '';
		try {
			const response = await api.listMemory({});
			if (response.success && !showMemoryPanel) {
				const notes = response.data?.notes || [];
				memoryNoteCount = notes.filter(
					(n) => n.scope === 'global' || (n.scope === 'preset' && n.scope_ref === presetId)
				).length;
			}
		} catch (err) {
			logger.error('Failed to load memory note count:', err);
		}
	}
	$: if (browser) {
		loadMemoryNoteCountAtRest(contextTab?.selectedPreset ?? null);
	}
	// null = not yet loaded (the Tools popover shows every mode tool, same as
	// before this feature existed); once loaded, tools this user can't see at
	// all (admin-disabled) are filtered out of that popover.
	let myToolPreferences: UserToolPreference[] | null = null;

	// Configuration data
	let llmConfigs: any[] = [];
	let selectedConfigId = '';
	let enableTools = browser ? scopedStorage.get(STORAGE_KEY_ENABLE_TOOLS) !== 'false' : true;
	let configsLoaded = false;
	let dataLoadInitiated = false;
	let recentSessions: ChatSessionResponse[] = [];
	let sessionsLoadInitiated = false;
	let sessionsRequestId = 0;
	let sessionLoadRequestId = 0;
	let loadingSessionId: string | null = null;
	let destroyed = false;

	// Vision/Image state
	let selectedImageData: { path: string; relative_path?: string; url: string; name: string; type: string } | null = null;

	// Phase 4: Auto-attach last generated image (only when it changes)
	let alwaysAttachLastImage = scopedStorage.get(STORAGE_KEY_ATTACH_IMAGE) === 'true';
	let lastAutoAttachedUrl: string | null = null;
	function toggleAttachLastImage() {
		alwaysAttachLastImage = !alwaysAttachLastImage;
		scopedStorage.set(STORAGE_KEY_ATTACH_IMAGE, alwaysAttachLastImage ? 'true' : 'false');
	}

	// Tab context pinning
	let pinnedTabId: string | null = scopedStorage.get(STORAGE_KEY_PINNED_TAB);

	function savePinnedTab(id: string | null) {
		pinnedTabId = id;
		if (id) {
			scopedStorage.set(STORAGE_KEY_PINNED_TAB, id);
		} else {
			scopedStorage.remove(STORAGE_KEY_PINNED_TAB);
		}
	}

	// All available tabs for pin dropdown
	$: allTabs = generationTabs($tabsStore.tabs || []);

	// Safety: clear stale pinnedTabId when it no longer references a valid tab
	$: if (pinnedTabId && allTabs.length > 0 && !allTabs.find((t: any) => t.id === pinnedTabId)) {
		savePinnedTab(null);
	}

	// Resolved context tab: pinned or active
	$: chatActiveTab = $activeToolTab ? null : $activeTab;
	$: contextTab = resolveChatContextTab(allTabs, pinnedTabId, chatActiveTab);

	let chatResources: PresetPromptResourcesResult = { specs: [], fieldLabels: {} };
	let chatResourcesKey = '';
	$: {
		const key = `${contextTab?.selectedPreset ?? ''}::${contextTab?.selectedMode ?? ''}::${contextTab?.selectedVariant ?? ''}`;
		if (key !== chatResourcesKey) {
			chatResourcesKey = key;
			chatResources = { specs: [], fieldLabels: {} };
			getPresetPromptResources(
				contextTab?.selectedPreset,
				contextTab?.selectedMode,
				contextTab?.selectedVariant ?? undefined
			).then((result) => {
				if (chatResourcesKey === key) chatResources = result;
			});
		}
	}

	async function resourceTokenContextFor(tab: typeof contextTab): Promise<ResourceTokenContext | undefined> {
		if (!tab) return undefined;
		const result = await getPresetPromptResources(tab.selectedPreset, tab.selectedMode, tab.selectedVariant ?? undefined);
		if (!result.specs.length) return undefined;
		return { specs: result.specs, formValues: tab.formData ?? {}, fieldLabels: result.fieldLabels };
	}

	// Preset display names for the context strip / pin picker, resolved once
	// (same lookup ChatMemoryPanel does per-preset via loadPresets().find) and
	// cached for every preset id at once since both surfaces need it for
	// every open tab, not just the current one.
	let presetNamesCache: Record<string, string> = {};
	let presetBatchCache: Record<string, boolean> = {};
	async function loadPresetNames() {
		try {
			const response = await loadPresets();
			if (response.success) {
				const map: Record<string, string> = {};
				const batch: Record<string, boolean> = {};
				for (const p of response.data || []) {
					map[p.id] = p.name;
					batch[p.id] = p.supports_prompt_batch === true;
				}
				presetNamesCache = map;
				presetBatchCache = batch;
			}
		} catch (err) {
			logger.error('Failed to load preset names:', err);
		}
	}
	function resolvePresetName(id: string): string | null {
		return presetNamesCache[id] ?? null;
	}
	$: tabPresetNames = Object.fromEntries(
		allTabs.map((t: any) => [t.id, t.selectedPreset ? resolvePresetName(t.selectedPreset) : null])
	);

	// Context strip: states which tab the chat is reading (see contextStrip.ts).
	$: pinnedTabResolved = pinnedTabId ? allTabs.find((t: any) => t.id === pinnedTabId) || null : null;
	$: stripModel = deriveContextStripModel({
		activeTab: chatActiveTab,
		pinnedTab: pinnedTabResolved,
		pinnedTabId,
		presetName: resolvePresetName
	});

	// Tab-switch moment: a quiet transcript divider + a transient strip flash,
	// fired only when FOLLOWING (pinning is a deliberate override, not a
	// "switch" to announce). Both are client-side scratch state, never
	// persisted, and cleared whenever the session view changes underneath
	// them since their message-index anchors would no longer point at
	// anything meaningful.
	interface ContextDivider {
		id: string;
		afterIndex: number;
		tabName: string;
		presetLabel: string | null;
		dims: string | null;
	}
	let contextDividers: ContextDivider[] = [];
	let dividerCounter = 0;
	let lastFollowedTabId: string | null = null;
	let dividerTrackedSessionId: string | null = null;
	let stripFlash = false;
	let stripFlashTimer: ReturnType<typeof setTimeout> | null = null;

	function triggerStripFlash() {
		stripFlash = true;
		if (stripFlashTimer) clearTimeout(stripFlashTimer);
		stripFlashTimer = setTimeout(() => {
			stripFlash = false;
			stripFlashTimer = null;
		}, 1200);
	}

	$: if (sessionId !== dividerTrackedSessionId) {
		contextDividers = [];
		dividerTrackedSessionId = sessionId;
	}

	$: if (browser && $activeTab) {
		const nextId = $activeTab.id;
		const switched = deriveTabSwitchDivider({
			previousTabId: lastFollowedTabId,
			activeTab: $activeTab,
			pinnedTabId,
			hasMessages: messages.length > 0,
			presetName: resolvePresetName
		});
		if (switched) {
			contextDividers = [
				...contextDividers,
				{
					id: `ctx-div-${++dividerCounter}`,
					afterIndex: messages.length - 1,
					tabName: switched.tabName,
					presetLabel: switched.presetLabel,
					dims: switched.dims
				}
			];
		}
		// lastFollowedTabId only advances while following -- frozen during a
		// pin so an active-tab change that happens unobserved (pinned) never
		// reads as a "switch" the moment the user later unpins.
		if (!pinnedTabId) {
			if (lastFollowedTabId !== null && lastFollowedTabId !== nextId) {
				triggerStripFlash();
			}
			lastFollowedTabId = nextId;
		}
	}

	// Images already loaded into the context tab's form / Video Director, for
	// the "attach from current form" strip below — tracks live form edits.
	$: formImageEntries = collectFormImages(contextTab);
	$: selectedDurablePath = selectedImageData
		? selectedImageData.relative_path || selectedImageData.path
		: null;

	// The context tab's most recent generation, for the attach popover's
	// "Use last generated image" row (Seam 1) — same source sendMessage's
	// auto-attach reads, just surfaced here for manual reuse too.
	$: lastGeneratedImage = (() => {
		const batchImages = contextTab?.generation?.batchImages;
		if (!batchImages || batchImages.length === 0) return null;
		const last = batchImages[batchImages.length - 1];
		const url = last.originalUrl || last.url;
		if (!url) return null;
		return { url, name: url.split('/').pop() || 'last_generated_image' };
	})();

	function attachLastGeneratedImage() {
		if (!lastGeneratedImage) return;
		selectedImageData = {
			path: lastGeneratedImage.url,
			relative_path: lastGeneratedImage.url,
			url: lastGeneratedImage.url,
			name: lastGeneratedImage.name,
			type: 'image'
		};
	}

	function removeSelectedImage() {
		selectedImageData = null;
	}

	// Selected LoRAs of the context tab's lora_picker field(s), for the @form
	// picker's browsable per-LoRA rows.
	$: loraSelectionsStore = loraSelectionsForTab(contextTab?.id);
	$: loraSelections = $loraSelectionsStore;

	// Video Director detection — mirrors generate/+page.svelte's videoDirectorActive
	// so the chat's segment-apply flow knows whether the context tab's
	// prompt lives in tab.promptSegments (standard editor) or
	// tab.videoDirector.global_prompt_segments (Director's persistent "Direction"
	// prompt). This panel mounts independently of the generate page (see
	// GlobalChatPanel/GenerationPanel) so it can't reuse that page's local cache
	// and fetches its own copy of the preset's vars, cached per preset id.
	let directorPresetVarsCache: Record<string, Record<string, any> | null> = {};
	const directorPresetVarsInFlight = new Set<string>();

	async function loadDirectorPresetVars(presetId: string) {
		if (!presetId || presetId in directorPresetVarsCache || directorPresetVarsInFlight.has(presetId)) return;
		directorPresetVarsInFlight.add(presetId);
		try {
			const response = await api.getPreset(presetId);
			directorPresetVarsCache = {
				...directorPresetVarsCache,
				[presetId]: response.success ? response.data?.vars || {} : null
			};
		} catch (err) {
			logger.error('Failed to load preset vars for Video Director detection:', err);
			directorPresetVarsCache = { ...directorPresetVarsCache, [presetId]: null };
		} finally {
			directorPresetVarsInFlight.delete(presetId);
		}
	}

	$: if (browser && contextTab?.selectedPreset) {
		loadDirectorPresetVars(contextTab.selectedPreset);
	}

	// Same overlay-resolved capabilities as generate/+page.svelte's mount gate
	// (resolveDirectorCapabilities) -- this feeds both `videoDirectorActive`
	// below and the form_state export's `doc: normalizeDirectorValue(...)`, so
	// fixing this one computation keeps both in sync with the active preset
	// mode. The cache key includes `selectedMode` for the same reason as there.
	let videoDirectorCapsCache: { key: string; caps: DirectorCapabilities | null } | null = null;
	$: videoDirectorCapsRaw = directorPresetVarsCache[contextTab?.selectedPreset || '']?.video_director;
	$: videoDirectorOverlay = (() => {
		const modelId = selectedCloudModelId(contextTab?.formData);
		return modelId ? directorOverlayFrom(sharedCapabilityCache.get(modelId)) : null;
	})();
	$: videoDirectorCapsKey = `${contextTab?.selectedPreset || ''}:${contextTab?.selectedMode || ''}:${JSON.stringify(videoDirectorCapsRaw ?? null)}:${JSON.stringify(videoDirectorOverlay)}`;
	$: videoDirectorCaps = (() => {
		if (videoDirectorCapsCache && videoDirectorCapsCache.key === videoDirectorCapsKey) {
			return videoDirectorCapsCache.caps;
		}
		const caps = parseDirectorCapabilities(
			applyModelOverlay(resolveDirectorRaw(videoDirectorCapsRaw, contextTab?.selectedMode ?? null), videoDirectorOverlay)
		);
		videoDirectorCapsCache = { key: videoDirectorCapsKey, caps };
		return caps;
	})();
	$: videoDirectorActive =
		!!videoDirectorCaps &&
		!!contextTab?.selectedMode &&
		(videoDirectorCaps.presetModes === null || videoDirectorCaps.presetModes.includes(contextTab.selectedMode));

	// Music Director detection -- same rationale and cache shape as Video
	// Director above (this panel fetches its own copy of the preset's vars,
	// cached per preset id, independent of generate/+page.svelte's cache).
	let musicDirectorCapsCache: { key: string; caps: MusicDirectorCapabilities | null } | null = null;
	$: musicDirectorCapsRaw = directorPresetVarsCache[contextTab?.selectedPreset || '']?.music_director;
	$: musicDirectorCapsKey = `${contextTab?.selectedPreset || ''}:${contextTab?.selectedMode || ''}:${JSON.stringify(musicDirectorCapsRaw ?? null)}`;
	$: musicDirectorCaps = (() => {
		if (musicDirectorCapsCache && musicDirectorCapsCache.key === musicDirectorCapsKey) {
			return musicDirectorCapsCache.caps;
		}
		const caps = resolveMusicDirectorCapabilities(musicDirectorCapsRaw, contextTab?.selectedMode ?? null);
		musicDirectorCapsCache = { key: musicDirectorCapsKey, caps };
		return caps;
	})();
	$: musicDirectorActive =
		!!musicDirectorCaps &&
		!!contextTab?.selectedMode &&
		(musicDirectorCaps.presetModes === null || musicDirectorCaps.presetModes.includes(contextTab.selectedMode));

	// Refs
	let inputRef: ChatInput;
	let messagesContainerRef: HTMLDivElement;

	// Get selected config and check vision support
	$: selectedConfig = llmConfigs.find((c) => c.id === selectedConfigId);
	$: supportsVision = selectedConfig?.supports_vision || false;

	// Tools visible in the current mode (mode tools + global tools)
	$: visibleTools = toolsForMode($chatModes.toolsCatalog, currentMode);

	// Token usage tracking: the header's context meter (Seam 3) only wants
	// the last request's prompt-token count, not a running total.
	$: currentContextSize = (() => {
		for (let i = messages.length - 1; i >= 0; i--) {
			if (messages[i].role === 'assistant' && messages[i].prompt_tokens) {
				return messages[i].prompt_tokens!;
			}
		}
		return 0;
	})();

	function saveCurrentSessionId() {
		if ($chatSession.sessionId) {
			saveActiveSessionId($chatSession.sessionId);
		}
	}

	// Save config selection when it changes
	$: if (configsLoaded && selectedConfigId) {
		saveToStorage(STORAGE_KEY_CONFIG, selectedConfigId);
	}

	// Tool governance is per LLM config - re-fetch preferences whenever the
	// composer's active config changes (including the initial resolve).
	$: if (configsLoaded && selectedConfigId) {
		void loadMyToolPreferences();
	}

	// Save enable tools toggle when it changes
	$: if (browser) {
		scopedStorage.set(STORAGE_KEY_ENABLE_TOOLS, enableTools ? 'true' : 'false');
	}

	// Load configs on mount
	$: if (browser && !dataLoadInitiated) {
		dataLoadInitiated = true;
		loadAllData();
	}

	// Load sessions after configs are loaded
	$: if (configsLoaded && !sessionsLoadInitiated) {
		sessionsLoadInitiated = true;
		void loadRecentSessions(true);
	}

	async function loadRecentSessions(restoreStored = false) {
		const requestId = ++sessionsRequestId;
		try {
			const response = await api.getChatSessions({ limit: 20 });
			if (destroyed || requestId !== sessionsRequestId) return;
			if (response.success) {
				recentSessions = response.data?.sessions || [];

				// Auto-load the single active session, regardless of its mode —
				// route only decides the mode of a fresh conversation.
				if (restoreStored && !$chatSession.sessionId) {
					const storedSessionId = loadActiveSessionId();
					if (storedSessionId) {
						const sessionExists = recentSessions.some(
							(s) => s.id === storedSessionId && s.status === 'active'
						);
						if (sessionExists) {
							await loadSession(storedSessionId);
						} else {
							saveActiveSessionId('');
						}
					}
				}
			}
		} catch (err) {
			if (destroyed || requestId !== sessionsRequestId) return;
			logger.error('Failed to load chat sessions:', err);
		}
	}

	async function loadAllData() {
		const [configs] = await Promise.all([
			loadConfigurations(),
			chatModes.load(),
			loadPresetNames()
		]);
		if (destroyed) return;
		llmConfigs = configs;

		// Resolve the chat mode from the current route (only for a fresh conversation;
		// a restored session keeps its own persisted mode)
		if (!$chatSession.sessionId && $chatSession.messages.length === 0) {
			const resolved = $declaredMode || resolveModeForRoute($page.url.pathname, $chatModes.modes);
			if (resolved !== $chatSession.mode) {
				chatSession.newConversation(resolved);
			}
		}

		selectedConfigId = resolveConfigId(llmConfigs, loadFromStorage(STORAGE_KEY_CONFIG));
		applyStoredDisabledToolsForMode($chatSession.mode);

		configsLoaded = true;

		await tick();
		inputRef?.focus();
	}

	// Tool governance (enabled/locked) is per LLM config - scope every fetch to
	// whichever config the composer currently has selected so the popover and
	// "My Tools" panel reflect that config's rows, not some other one's.
	async function loadMyToolPreferences() {
		// The endpoint requires an active config to scope against; nothing to
		// fetch yet if none is selected (e.g. before the first config loads).
		if (!selectedConfigId) return;
		try {
			const response = await api.getMyToolsetPreferences(selectedConfigId);
			if (destroyed) return;
			if (response.success && response.data) {
				myToolPreferences = response.data;
			}
		} catch (err) {
			logger.error('Failed to load tool preferences:', err);
		}
	}

	// Seed the subtractive tool filter from this mode's persisted selection.
	// The chatSession store resets disabledTools to [] on every new/loaded
	// conversation, so this must run after each of those transitions.
	function applyStoredDisabledToolsForMode(mode: string) {
		chatSession.patch({ disabledTools: loadDisabledTools(mode) });
	}

	// Unlike mode, the LLM config can be switched mid-session. The session's
	// llm_config_id is what the backend actually sends the next turn to, so an
	// already-created session must be rebound server-side too — otherwise the
	// composer's selection is cosmetic and gets clobbered by the session's
	// original config the next time it's loaded (e.g. on refresh).
	function handleSelectConfig(id: string) {
		selectedConfigId = id;
		if (sessionId) {
			void updateSessionLLMConfig(sessionId, id);
		}
	}

	async function updateSessionLLMConfig(id: string, llmConfigId: string) {
		try {
			const response = await api.updateChatSession(id, { llm_config_id: llmConfigId });
			if (!response.success) {
				logger.error('Failed to persist LLM config selection on session:', response.message);
			}
		} catch (err) {
			logger.error('Failed to persist LLM config selection on session:', err);
		}
	}

	function handleSelectMode(modeId: string) {
		if ($chatSession.messages.length > 0 || modeId === $chatSession.mode) return;
		chatComposerDrafts.clear(sessionId);
		chatSession.newConversation(modeId);
		applyStoredDisabledToolsForMode(modeId);
	}

	function handleToggleEnableTools(enabled: boolean) {
		enableTools = enabled;
	}

	function handleToggleTool(name: string) {
		const disabled = $chatSession.disabledTools;
		const next = disabled.includes(name)
			? disabled.filter((n) => n !== name)
			: [...disabled, name];
		chatSession.patch({ disabledTools: next });
		saveDisabledTools($chatSession.mode, next);
	}

	function handleNewSession() {
		const resolved = $declaredMode || resolveModeForRoute($page.url.pathname, $chatModes.modes);
		chatComposerDrafts.clear(sessionId);
		chatSession.newConversation(resolved);
		saveActiveSessionId('');
		applyStoredDisabledToolsForMode(resolved);
		loadRecentSessions();
	}

	/** Builds the dependency object `turnController`'s functions need — the
	 * real chatSession store and api transports, plus a few UI-only callbacks
	 * for state this component owns and the controller module has no
	 * business touching (recentSessions, selectedImageData). */
	function buildTurnControllerDeps(): TurnControllerDeps {
		return {
			store: chatSession,
			api,
			scrollToBottom,
			scrollToBottomIfPinned,
			tick,
			logError: (message, err) => logger.error(message, err),
			onTitle: (sessionId, name) => {
				recentSessions = applyTitle(recentSessions, sessionId, name);
			},
			onAnswered: () => {
				selectedImageData = null;
			}
		};
	}

	async function loadSession(id: string) {
		if (loadingSessionId === id) return;
		if ($chatSession.sessionId === id && $chatSession.messages.length > 0) {
			return;
		}

		const requestId = ++sessionLoadRequestId;
		loadingSessionId = id;
		try {
			const response = await api.getChatSession(id, { tail: TRANSCRIPT_TAIL });
			if (destroyed || requestId !== sessionLoadRequestId) return;

			if (response.success && response.data) {
				const loadedMessages = response.data.messages.map(mapPersistedMessage);

				chatSession.loadedSession(
					{
						id: response.data.id,
						mode: response.data.mode
					},
					loadedMessages,
					{ messageCount: response.data.message_count, hasEarlier: response.data.has_earlier ?? false }
				);
				applyStoredDisabledToolsForMode(response.data.mode);
				saveCurrentSessionId();

				if (response.data.llm_config_id) {
					selectedConfigId = response.data.llm_config_id;
				}

				await tick();
				scrollToBottom();

				// A turn was still streaming when we loaded — resume it so a
				// mid-response reload doesn't dead-end on a lost reply. The
				// user message for that turn is always persisted before its
				// assistant reply, so it's already the trailing message here;
				// pass its id through as the recovery target identity (see
				// findTurnAssistantMessage) — there's no message_created event
				// to learn it from when reattaching to an evicted, finished turn.
				if (response.data.active_turn?.status === 'running') {
					const trailing = loadedMessages[loadedMessages.length - 1];
					const activeUserMessageId = trailing?.role === 'user' ? trailing.id : undefined;
					void turnControllerReattachToTurn(buildTurnControllerDeps(), {
					sessionId: response.data.id,
					initialUserMessageId: activeUserMessageId
				});
				}
			} else {
				chatSession.patch({ error: 'Failed to load session' });
			}
		} catch (err) {
			if (destroyed || requestId !== sessionLoadRequestId) return;
			logger.error('Failed to load session:', err);
			chatSession.patch({ error: 'Failed to load session' });
		} finally {
			if (loadingSessionId === id) loadingSessionId = null;
		}
	}

	// A same-tick re-entrancy guard independent of `$chatSession.loadingEarlier`
	// — the IntersectionObserver callback below can fire again before a store
	// subscription update has actually landed on this read.
	let loadEarlierInFlight = false;

	/** Pages the currently loaded window one `TRANSCRIPT_TAIL`-sized step
	 * further back in history, prepending it and keeping the reader's eye on
	 * the same message they were looking at (scrollTop shifts by exactly the
	 * height the prepended content added). */
	async function loadEarlier() {
		const state = $chatSession;
		if (!state.sessionId || !state.hasEarlier || state.loadingEarlier || loadEarlierInFlight) return;
		const earliestLoaded = state.messages[0];
		if (!earliestLoaded?.id) return; // nothing persisted to page before yet

		loadEarlierInFlight = true;
		chatSession.patch({ loadingEarlier: true });
		const container = messagesContainerRef;
		const previousScrollHeight = container?.scrollHeight ?? 0;

		try {
			const response = await api.getChatMessagesBefore(state.sessionId, earliestLoaded.id, TRANSCRIPT_TAIL);
			if (destroyed || $chatSession.sessionId !== state.sessionId) return;

			if (response.success && response.data) {
				const olderMessages = response.data.messages.map(mapPersistedMessage);
				chatSession.prependMessages(olderMessages, response.data.has_earlier);
				// Every existing divider's position shifts by however many
				// messages just landed in front of it.
				if (olderMessages.length > 0) {
					contextDividers = contextDividers.map((d) => ({
						...d,
						afterIndex: d.afterIndex + olderMessages.length
					}));
				}
				await tick();
				if (container) {
					// Never animated: this is a silent position correction for
					// content that just appeared above the fold, not a scroll the
					// reader should see happen.
					jumpTo(container, container.scrollTop + (container.scrollHeight - previousScrollHeight));
				}
			} else {
				// Backend envelope failure (e.g. `message_not_found` if the
				// earliest loaded message was itself since deleted) — leave the
				// loaded window exactly as-is, no banner: surfacing `error` here
				// would show the persistent chat-error-banner for what's a
				// background pagination hiccup, not a turn failure, and the row
				// stays visible/clickable to retry.
				logger.error('Failed to load earlier messages:', response.error);
				chatSession.patch({ loadingEarlier: false });
			}
		} catch (err) {
			logger.error('Failed to load earlier messages:', err);
			if (!destroyed && $chatSession.sessionId === state.sessionId) {
				chatSession.patch({ loadingEarlier: false });
			}
		} finally {
			loadEarlierInFlight = false;
		}
	}

	/** Auto-triggers loadEarlier() once the "Load earlier messages" row
	 * scrolls into view, so reading up to the top of the loaded window pages
	 * further back without an explicit click. */
	function loadEarlierOnIntersect(node: HTMLElement) {
		const observer = new IntersectionObserver(
			(entries) => {
				if (entries[0]?.isIntersecting) void loadEarlier();
			},
			{ root: messagesContainerRef ?? null }
		);
		observer.observe(node);
		return { destroy: () => observer.disconnect() };
	}

	onDestroy(() => {
		destroyed = true;
		sessionsRequestId += 1;
		sessionLoadRequestId += 1;
		if (stripFlashTimer) clearTimeout(stripFlashTimer);
		if (pinnedScrollHandle !== null) cancelFrame(pinnedScrollHandle);
		pendingCardObserver?.disconnect();
	});

	async function handleCommand(command: string): Promise<boolean> {
		const cmd = command.toLowerCase().trim();
		if (cmd === '/tools') {
			try {
				const response = await api.listChatTools($chatSession.mode);
				const tools = response.data?.tools || [];
				if (tools.length === 0) {
					addSystemMessage('No tools available.');
				} else {
					const lines = tools.map((t: any) => `- ${t.name}`);
					addSystemMessage(`Available LLM tools (${tools.length}):\n\n${lines.join('\n')}`);
				}
			} catch {
				addSystemMessage('Failed to fetch tool list.');
			}
			return true;
		}
		if (cmd === '/help') {
			addSystemMessage(
				'Available commands:\n\n' +
					'**/tools** — List all tools the AI can use\n' +
					'**/help** — Show this help message'
			);
			return true;
		}
		return false;
	}

	function addSystemMessage(content: string) {
		chatSession.addMessage({
			role: 'assistant' as const,
			content,
			timestamp: Date.now(),
			isSystem: true
		});
		tick().then(scrollToBottom);
	}

	async function handleStop() {
		const id = $chatSession.sessionId;
		if (!id) return;
		try {
			await api.cancelChatTurn(id);
		} catch (err) {
			logger.error('Failed to cancel turn:', err);
		}
	}

	async function handleSend() {
		if (!userInput.trim() || $chatSession.isGenerating) return;

		const instruction = userInput.trim();
		// Snapshot attached @resources; sent as [{uri}] alongside the message text
		const attachedResources = Object.values(userResources);
		const resourceRefs = attachedResources.map((r) => ({ uri: r.uri }));

		// Handle local commands
		if (instruction.startsWith('/')) {
			userInput = '';
			userResources = {};
			const handled = await handleCommand(instruction);
			if (handled) return;
		}

		if (!selectedConfigId) return;

		userInput = '';
		userResources = {};
		await sendMessage(instruction, { resourceRefs, attachedResources });
	}

	$: promptBatchOn =
		!videoDirectorActive && !!contextTab?.selectedPreset && presetBatchCache[contextTab.selectedPreset] === true;

	function buildContextMetadata(): Record<string, any> {
		const tab = contextTab;
		const activeSegments: Segment[] = videoDirectorActive ? [] : tab?.promptSegments || [];
		const negativeSegments: Segment[] = videoDirectorActive ? [] : tab?.negativePromptSegments || [];
		const mapSegment = (seg: Segment, i: number, negative: boolean) => ({
			index: i,
			id: seg.id,
			content: seg.content,
			name: seg.name || seg.title || null,
			type: seg.type || 'content',
			enabled: isSegmentEnabled(seg),
			...(seg.prefix ? { prefix: seg.prefix } : {}),
			...(seg.suffix ? { suffix: seg.suffix } : {}),
			...(seg.template ? { template: seg.template } : {}),
			...(negative ? { negative: true } : {})
		});
		const generatePageActive = isGeneratePageContext(currentPageMode);
		const generationPresetActive = isGenerationPresetContext(currentMode);
		const contextMetadata: Record<string, any> = {
			segments: [
				...activeSegments.map((seg, i) => mapSegment(seg, i, false)),
				...negativeSegments.map((seg, i) => mapSegment(seg, activeSegments.length + i, true))
			],
			form_state: buildChatFormState({
				tab,
				generatePageActive,
				generationPresetActive,
				sessionId: chatFormSessionId(tab, currentMode),
				videoDirector: videoDirectorActive
					? {
							active: true,
							doc: normalizeDirectorValue(tab?.videoDirector, videoDirectorCaps!),
							capabilities: videoDirectorCapsRaw
						}
					: { active: false, doc: null, capabilities: null },
				musicDirector: musicDirectorActive
					? {
							active: true,
							doc: normalizeMusicDirectorValue(tab?.musicDirector, musicDirectorCaps!),
							capabilities: musicDirectorCapsRaw
						}
					: { active: false, doc: null, capabilities: null }
			})
		};
		if (promptBatchOn && tab) {
			const active = activePromptIndex(tab);
			contextMetadata.form_state.prompt_batch = { supported: true, count: promptCount(tab), active };
			contextMetadata.prompt_batch = {
				active,
				prompts: promptList(tab).map((entry, index) => {
					const positive = entry.promptSegments || [];
					return {
						index,
						name: entry.name?.trim() || '',
						segments: [
							...positive.map((seg, i) => mapSegment(seg, i, false)),
							...(entry.negativePromptSegments || []).map((seg, i) => mapSegment(seg, positive.length + i, true))
						]
					};
				})
			};
		}
		Object.assign(contextMetadata, collectProvidedContext());
		return contextMetadata;
	}

	/**
	 * Send `instruction` as a user turn through the same streaming pipeline
	 * `handleSend` drives — factored out so a programmatic send (answering a
	 * docked question) goes through the exact request/response handling
	 * instead of faking composer input state. `handleSend` owns the composer
	 * (userInput/userResources, the `/command` gate); this owns everything
	 * downstream of "an instruction is ready to go out".
	 */
	/**
	 * Send `instruction` as a new turn. Ownership, session creation, the
	 * streaming/fallback/reattach decision, and every publication's staleness
	 * guard live in `turnController.sendMessage` (see its own doc comment);
	 * this wrapper's only job is to compute the UI/segment-specific request
	 * payload (auto-attach, contextMetadata) and the plugin-only side effects
	 * (recentSessions, input focus) the controller module has no business
	 * owning.
	 */
	async function sendMessage(
		instruction: string,
		options: { resourceRefs?: { uri: string }[]; attachedResources?: ResourceChipData[] } = {}
	) {
		if (!instruction.trim() || $chatSession.isGenerating) return;
		if (!selectedConfigId) return;
		const resourceRefs = options.resourceRefs || [];
		const attachedResources = options.attachedResources || [];

		// Auto-attach last generated image only when it changes. Computed once,
		// up front — both the optimistic user message and the request payload
		// need the SAME `effectiveImageData`, and `lastAutoAttachedUrl`'s
		// update must only happen once.
		let autoAttachedImage: typeof selectedImageData = null;
		if (alwaysAttachLastImage && !selectedImageData && supportsVision) {
			const tab = contextTab;
			const batchImages = tab?.generation?.batchImages;
			if (batchImages && batchImages.length > 0) {
				const lastImage = batchImages[batchImages.length - 1];
				const imagePath = lastImage.originalUrl || lastImage.url;
				if (imagePath && imagePath !== lastAutoAttachedUrl) {
					autoAttachedImage = {
						path: imagePath,
						relative_path: imagePath,
						url: imagePath,
						name: 'last_generated_image',
						type: 'image'
					};
					lastAutoAttachedUrl = imagePath;
				}
			}
		}
		const effectiveImageData = selectedImageData || autoAttachedImage;

		// Local resource labels let @chips render before the backend echoes
		// the resolved snapshot back in message metadata.
		const tempUserMessage: ChatMessageData = {
			role: 'user',
			content: instruction,
			timestamp: Date.now(),
			imageUrl: effectiveImageData?.url || null,
			metadata: attachedResources.length
				? { resources: attachedResources.map((r) => ({ uri: r.uri, title: r.label })) }
				: undefined
		};

		// Subtractive tool filter sent as-is: the global toggle and the
		// per-mode unticked names, never a materialized allowlist — a tool the
		// backend adds after this page's catalog was fetched still reaches the
		// session as long as the user never unticked it.
		const disabledToolsPayload = $chatSession.disabledTools;

		function buildPayload(): SendMessagePayload {
			const contextMetadata = buildContextMetadata();
			if (effectiveImageData?.url) {
				contextMetadata.image_url = effectiveImageData.url;
			}
			return {
				content: instruction,
				imageData: effectiveImageData?.relative_path || effectiveImageData?.path || undefined,
				contextMetadata,
				resources: resourceRefs,
				timeoutSeconds: selectedConfig?.timeout
			};
		}

		const pendingRef = latestPendingExecution($chatSession.messages);
		if (pendingRef) {
			chatSession.updateMessages((msgs) => markExecutionRejected(msgs, pendingRef, instruction));
		}

		await turnControllerSendMessage(buildTurnControllerDeps(), {
			instruction,
			tempUserMessage,
			buildPayload,
			createSessionPayload: {
				llm_config_id: selectedConfigId || undefined,
				mode: $chatSession.mode,
				tools_enabled: enableTools,
				disabled_tools: disabledToolsPayload.length > 0 ? disabledToolsPayload : undefined
			},
			onSessionCreated: (sessionData) => {
				saveCurrentSessionId();
				recentSessions = [sessionData, ...recentSessions];
			},
			onSettled: () => {
				inputRef?.focus();
			}
		});
	}

	async function handleApplySegmentAction(
		action: {
			type: string;
			segmentIndex: number;
			segmentId: string;
			content: string;
			/** Video Director timeline style only: which shot `segmentId` (a
			 * beat) belongs to -- parsed off the tag by markdown.ts's
			 * `parseToolActions` (`ToolAction.shotId`). See
			 * `applyDirectorSegmentPrompt`'s own doc comment in
			 * utils/videoDirector.ts for the resolution rule this mirrors. */
			shotId?: string;
			list?: SegmentListKey;
			promptIndex?: number;
			name?: string;
		},
		messageId: string,
		actionIndex: number
	) {
		const tab = contextTab;
		if (!tab) return;
		if (!canApplySegmentAction(action)) return;

		if (isPromptListAction(action.type)) {
			await applyPromptAction(tab, action, messageId, actionIndex);
			return;
		}
		if (
			action.promptIndex !== undefined &&
			promptBatchOn &&
			action.promptIndex !== activePromptIndex(tab) &&
			(action.type === 'update_segment' || action.type === 'add_segment' || action.type === 'delete_segment')
		) {
			await applySegmentToOtherPrompt(tab, action, messageId, actionIndex);
			return;
		}

		if (action.type === 'add_segment' || action.type === 'delete_segment') {
			await applySegmentListAction(tab, action, messageId, actionIndex);
			return;
		}

		if (action.type === 'update_director_segment') {
			if (!videoDirectorActive || !videoDirectorCaps) return;
			const doc = normalizeDirectorValue(tab.videoDirector, videoDirectorCaps);
			const target = resolveDirectorSegmentTarget(doc, videoDirectorCaps, action);
			const next = applyDirectorSegmentPrompt(doc, videoDirectorCaps, action);
			if (!next || !target) return;
			tabsStore.updateTab(tab.id, { videoDirector: next });
			appliedSegmentActions.set(target.id, messageId, actionIndex);
			return;
		}

		// In Video Director mode tab.promptSegments is never populated —
		// the Director editor's persistent prompt lives at
		// tab.videoDirector.global_prompt_segments. Applying against the wrong
		// array used to silently no-op (idx out of range against an empty array).
		const useDirectorTarget = videoDirectorActive && !!videoDirectorCaps;
		const sourceSegments: Segment[] = useDirectorTarget
			? tab.videoDirector?.global_prompt_segments || []
			: tab.promptSegments || [];

		const result = await applySegmentUpdate(sourceSegments, action, await resourceTokenContextFor(tab));
		if (!result) return;
		const { segments, index: idx } = result;

		if (useDirectorTarget) {
			const doc = normalizeDirectorValue(tab.videoDirector, videoDirectorCaps!);
			tabsStore.updateTab(tab.id, {
				videoDirector: {
					...doc,
					global_prompt_segments: segments,
					global_prompt: flattenRichSegments(segments)
				}
			});
		} else {
			tabsStore.updateTab(tab.id, {
				promptSegments: segments,
				prompt: flattenRichSegments(segments)
			});
		}

		lastAppliedSegment.set(segments[idx].id);
		appliedSegmentActions.set(segments[idx].id, messageId, actionIndex);
	}

	function segmentListsOf(tab: { promptSegments?: Segment[]; negativePromptSegments?: Segment[] }): SegmentLists {
		return { positive: tab.promptSegments || [], negative: tab.negativePromptSegments || [] };
	}

	function commitSegmentList(tabId: string, lists: SegmentLists, list: SegmentListKey) {
		tabsStore.updateTab(
			tabId,
			list === 'positive'
				? { promptSegments: lists.positive, prompt: flattenRichSegments(lists.positive) }
				: { negativePromptSegments: lists.negative, negativePrompt: flattenRichSegments(lists.negative) }
		);
	}

	async function applySegmentListAction(
		tab: NonNullable<typeof contextTab>,
		action: { type: string; segmentIndex: number; segmentId: string; content: string; list?: SegmentListKey },
		messageId: string,
		actionIndex: number
	) {
		if (videoDirectorActive) return;
		const lists = segmentListsOf(tab);

		if (action.type === 'add_segment') {
			const result = await applySegmentAdd(lists, action, await resourceTokenContextFor(tab));
			if (!result) return;
			commitSegmentList(tab.id, result.lists, result.list);
			const added = result.lists[result.list][result.index];
			lastAppliedSegment.set(added.id);
			appliedSegmentActions.set(added.id, messageId, actionIndex);
			return;
		}

		const result = applySegmentDelete(lists, action);
		if (!result) return;
		commitSegmentList(tab.id, result.lists, result.removal.list);
		removedSegmentActions.set(messageId, actionIndex, result.removal);
		appliedSegmentActions.set(result.removal.segment.id, messageId, actionIndex);
	}

	async function applyPromptAction(
		tab: NonNullable<typeof contextTab>,
		action: { type: string; promptIndex?: number; name?: string; content: string },
		messageId: string,
		actionIndex: number
	) {
		if (!promptBatchOn) return;
		const result = await applyPromptListAction(tab, action, await resourceTokenContextFor(tab));
		if (!result) return;
		tabsStore.updateTab(tab.id, result.patch);
		promptActionUndos.set(messageId, actionIndex, result.undo);
		appliedSegmentActions.set(promptActionMarkerKey(messageId, actionIndex), messageId, actionIndex);
	}

	async function applySegmentToOtherPrompt(
		tab: NonNullable<typeof contextTab>,
		action: { type: string; segmentIndex: number; segmentId: string; content: string; list?: SegmentListKey; promptIndex?: number },
		messageId: string,
		actionIndex: number
	) {
		const result = await applySegmentActionToPrompt(tab, action.promptIndex!, action, await resourceTokenContextFor(tab));
		if (!result) return;
		tabsStore.updateTab(tab.id, result.patch);
		if (result.removal) removedSegmentActions.set(messageId, actionIndex, result.removal);
		appliedSegmentActions.set(result.segmentId, messageId, actionIndex);
	}

	function handleUndoPromptAction(messageId: string, actionIndex: number): boolean {
		const tab = contextTab;
		const undo = $promptActionUndos[removedActionKey(messageId, actionIndex)];
		if (!undo) return false;
		if (!tab) return true;
		const patch = undoPromptListAction(tab, undo);
		if (!patch) {
			toasts.info("This prompt changed; undo isn't available");
			return true;
		}
		tabsStore.updateTab(tab.id, patch);
		appliedSegmentActions.clear(promptActionMarkerKey(messageId, actionIndex));
		promptActionUndos.clear(messageId, actionIndex);
		return true;
	}

	function handleUndoSegmentAction(messageId: string, actionIndex: number) {
		if (handleUndoPromptAction(messageId, actionIndex)) return;
		const tab = contextTab;
		const removal = $removedSegmentActions[removedActionKey(messageId, actionIndex)];
		if (!tab || !removal || videoDirectorActive) return;
		if (removal.prompt !== undefined) {
			const patch = restoreSegmentInPrompt(tab, removal.prompt, removal);
			if (!patch) return;
			tabsStore.updateTab(tab.id, patch);
			appliedSegmentActions.clear(removal.segment.id);
			removedSegmentActions.clear(messageId, actionIndex);
			return;
		}
		const restored = restoreSegment(segmentListsOf(tab), removal);
		commitSegmentList(tab.id, restored, removal.list);
		appliedSegmentActions.clear(removal.segment.id);
		removedSegmentActions.clear(messageId, actionIndex);
		lastAppliedSegment.set(removal.segment.id);
	}

	function segmentTextForAction(action: { segmentId: string; segmentIndex: number; promptIndex?: number }): string | null {
		const tab = contextTab;
		if (!tab) return null;
		if (action.promptIndex !== undefined && action.promptIndex !== activePromptIndex(tab)) {
			return segmentTextInPrompt(tab, action.promptIndex, action);
		}
		const lists = segmentListsOf(tab);
		const found = locateSegmentInLists(lists, action);
		return found ? lists[found.list][found.index].content : null;
	}

	async function handlePromptFeedback(
		messageId: string,
		data: { actionIndex: number; verdict: 'approved' | 'rejected'; reason?: string }
	) {
		const id = $chatSession.sessionId;
		if (!id || !messageId) return;

		// Optimistic local update so state survives without waiting for the round trip
		chatSession.updateMessages((msgs) =>
			msgs.map((m) => {
				if (m.id !== messageId) return m;
				const prevMetadata = m.metadata || {};
				const prevFeedback = prevMetadata.prompt_feedback || {};
				return {
					...m,
					metadata: {
						...prevMetadata,
						prompt_feedback: {
							...prevFeedback,
							[data.actionIndex]: { verdict: data.verdict, reason: data.reason }
						}
					}
				};
			})
		);

		try {
			const response = await api.sendPromptFeedback(
				id,
				messageId,
				data.actionIndex,
				data.verdict,
				data.reason
			);
			if (!response.success) {
				chatSession.patch({ error: response.error || 'Failed to save feedback' });
			}
		} catch (err: any) {
			logger.error('Failed to send prompt feedback:', err);
			chatSession.patch({ error: err.message || 'Failed to save feedback' });
		}
	}

	async function handleResolveAction(
		messageId: string,
		index: number,
		decision: ActionDecision
	): Promise<ActionResolveOutcome> {
		const id = $chatSession.sessionId;
		if (!id) throw new Error('This conversation is not saved yet.');
		if ($chatSession.isGenerating) throw new Error('Wait for the current reply to finish.');
		setActionError(messageId, index, null);
		const response = await api.resolveToolExecution(id, {
			message_id: messageId,
			tool_index: index,
			approved: decision.approved,
			...(decision.reason ? { reason: decision.reason } : {}),
			...(decision.force ? { force: true } : {}),
			context_metadata: buildContextMetadata()
		});
		if (!response.success || !response.data) {
			throw new Error(response.message || response.error || 'Could not resolve this action.');
		}
		const { status, execution } = response.data;
		if ($chatSession.sessionId !== id || status === 'stale') return { status };
		chatSession.updateMessages((msgs) => replaceExecution(msgs, messageId, index, execution));
		if (status === 'already_resolved') return { status };
		const note = status === 'approved' ? await applyApprovedExecution(execution) : null;
		if (!$chatSession.isGenerating && $chatSession.sessionId === id) {
			void continueAfterResolve(messageId, index);
		}
		return { status, note };
	}

	async function applyApprovedExecution(execution: ToolExecution): Promise<string | null> {
		window.dispatchEvent(
			new CustomEvent('potionui:tool-approved', {
				detail: { tool_name: execution.tool_name, result: execution.result }
			})
		);
		if (!enableTools) return null;
		let resultData: any;
		try {
			resultData = JSON.parse(execution.result?.data || '');
		} catch {
			return null;
		}
		try {
			const outcome = dispatchToolApplied(execution.tool_name, resultData);
			if (outcome) {
				if (typeof outcome === 'object' && (outcome.status === 'stale' || outcome.status === 'partial')) {
					return outcome.message || 'Only part of this change could be applied.';
				}
				return null;
			}
			if (resultData.action === 'apply_form_changes') handleFormChangesApplied(resultData.applied_changes);
			if (resultData.action === 'apply_music_director_ops') handleMusicDirectorApplied(resultData.operations);
			if (resultData.action === 'set_prompt_relay') handlePromptRelaySet(resultData);
			if (resultData.action === 'apply_segment_updates') await handleSegmentUpdatesApplied(resultData.updates);
			return null;
		} catch (err) {
			logger.error('Failed to apply the approved change:', err);
			return getErrorMessage(err, 'The change was approved but could not be applied here.');
		}
	}

	let actionErrors: Record<string, string> = {};

	function setActionError(messageId: string, index: number, message: string | null) {
		const key = `${messageId}:${index}`;
		if (message) {
			actionErrors = { ...actionErrors, [key]: message };
		} else if (key in actionErrors) {
			const { [key]: _removed, ...rest } = actionErrors;
			actionErrors = rest;
		}
	}

	async function continueAfterResolve(messageId: string, index: number) {
		if (!selectedConfigId) return;
		await turnControllerSendMessage(buildTurnControllerDeps(), {
			instruction: '',
			recoveryAnchorMessageId: messageId,
			onRejected: (rejection) => setActionError(messageId, index, continuationErrorText(rejection)),
			buildPayload: () => ({
				content: '',
				contextMetadata: buildContextMetadata(),
				continueFrom: { message_id: messageId, tool_index: index },
				timeoutSeconds: selectedConfig?.timeout
			}),
			createSessionPayload: {
				llm_config_id: selectedConfigId || undefined,
				mode: $chatSession.mode,
				tools_enabled: enableTools
			},
			onSettled: () => {
				inputRef?.focus();
			}
		});
	}

	function handleAskRedo(execution: ToolExecution | undefined) {
		if (!execution) return;
		void sendMessage(redoRequestText(actionToolLabel(execution.tool_name, $chatModes.toolsCatalog)));
	}

	let pendingCardVisible = true;
	let pendingCardObserver: IntersectionObserver | null = null;
	let observedPendingCard: Element | null = null;

	function pendingCardElement(): HTMLElement | null {
		if (!pendingAction || !messagesContainerRef) return null;
		return messagesContainerRef.querySelector<HTMLElement>(
			`[data-action-key="${pendingAction.messageId}:${pendingAction.index}"]`
		);
	}

	async function syncPendingCardObserver() {
		await tick();
		if (destroyed) return;
		const node = pendingCardElement();
		if (node === observedPendingCard) return;
		pendingCardObserver?.disconnect();
		pendingCardObserver = null;
		observedPendingCard = node;
		pendingCardVisible = true;
		if (!node || typeof IntersectionObserver === 'undefined') return;
		pendingCardObserver = new IntersectionObserver(
			(entries) => {
				const entry = entries[entries.length - 1];
				if (entry) pendingCardVisible = entry.isIntersecting;
			},
			{ root: messagesContainerRef ?? null }
		);
		pendingCardObserver.observe(node);
	}

	$: messages, pendingAction, browser && void syncPendingCardObserver();

	function jumpToPendingAction() {
		const node = pendingCardElement();
		if (!node) return;
		const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
		node.scrollIntoView({ block: 'center', behavior: reduceMotion ? 'auto' : 'smooth' });
		node.focus({ preventScroll: true });
	}

	async function handleSegmentUpdatesApplied(
		updates: Array<{ segment_id: string; segment_index: number; content: string }>
	) {
		const tab = contextTab;
		if (!tab || !updates || updates.length === 0) return;

		let segments: Segment[] = tab.promptSegments || [];
		let lastAppliedId: string | null = null;
		const resourceContext = await resourceTokenContextFor(tab);
		for (const update of updates) {
			const result = await applySegmentUpdate(
				segments,
				{
					segmentId: update.segment_id,
					segmentIndex: update.segment_index,
					content: update.content
				},
				resourceContext
			);
			if (!result) continue;
			segments = result.segments;
			lastAppliedId = segments[result.index].id;
		}

		tabsStore.updateTab(tab.id, { promptSegments: segments, prompt: flattenRichSegments(segments) });
		if (lastAppliedId) lastAppliedSegment.set(lastAppliedId);
	}

	function handleFormChangesApplied(changes: Array<{ field_name: string; new_value: any }>) {
		const tab = contextTab;
		if (!tab || !changes || changes.length === 0) return;
		const updatedFormData = { ...tab.formData };
		for (const change of changes) {
			updatedFormData[change.field_name] = change.new_value;
		}
		tabsStore.updateTab(tab.id, { formData: updatedFormData });
	}

	function handleMusicDirectorApplied(operations: unknown[]) {
		const tab = contextTab;
		if (!tab || !musicDirectorCaps) return;
		const doc = normalizeMusicDirectorValue(tab.musicDirector, musicDirectorCaps);
		const next = applyMusicDirectorOperations(doc, operations, musicDirectorCaps);
		tabsStore.updateTab(tab.id, { musicDirector: normalizeMusicDirectorValue(next, musicDirectorCaps) });
	}

	let _relaySegCounter = 0;
	function handlePromptRelaySet(data: {
		global_prompt?: string;
		timeline: { duration: number; fps: number; segments: Array<{ start: number; end: number; text: string }> };
	}) {
		const tab = contextTab;
		if (!tab || !data?.timeline) return;
		const existing = tab.promptRelay?.timeline;
		const segments = (data.timeline.segments || []).map((s) => ({
			id: `ai-seg-${++_relaySegCounter}`,
			start: s.start,
			end: s.end,
			text: s.text
		}));
		tabsStore.updateTab(tab.id, {
			promptRelay: {
				global_prompt: data.global_prompt ?? '',
				timeline: {
					duration: data.timeline.duration,
					fps: data.timeline.fps,
					segments,
					// Preserve any media the user already placed on the timeline
					imageSegments: existing?.imageSegments ?? [],
					audioSegments: existing?.audioSegments ?? []
				}
			}
		});
	}

	function handleClose() {
		selectedImageData = null;
		chatSession.patch({ error: '' });
		onClose?.();
	}

	// Called by the history view after it deletes a session on the backend.
	function handleSessionDeleted(id: string) {
		recentSessions = recentSessions.filter((s) => s.id !== id);

		chatComposerDrafts.clear(id);

		if ($chatSession.sessionId === id) {
			const resolved = $declaredMode || resolveModeForRoute($page.url.pathname, $chatModes.modes);
			chatSession.newConversation(resolved);
			applyStoredDisabledToolsForMode(resolved);
			saveActiveSessionId('');
		}
	}

	// Header identity: title comes from the session record (recentSessions
	// already tracks the async LLM-generated title via the 'title' SSE event,
	// see createStreamEventHandler), not from chatSession itself, which has no
	// name field of its own.
	$: currentSessionRecord = sessionId ? recentSessions.find((s) => s.id === sessionId) : null;
	$: conversationTitle = currentSessionRecord?.name || (sessionId ? 'Untitled conversation' : 'New conversation');
	// Scoped to whatever's currently LOADED — with the transcript windowed to
	// the last TRANSCRIPT_TAIL messages, an image attached further back than
	// that isn't counted here. Cosmetic (a header subtitle), not corrected
	// against `$chatSession.messageCount` the way the message count below is:
	// doing so would need the backend to report an image count on the
	// session summary, which is outside this contract.
	$: threadImageCount = messages.filter((m) => m.role === 'user' && m.imageUrl).length;
	$: headerSubtitle = threadImageCount > 0
		? `${threadImageCount} image${threadImageCount === 1 ? '' : 's'} attached in thread`
		: sessionId
			? `${$chatSession.messageCount || messages.filter((m) => !m.isSystem).length} messages`
			: '';

	async function handleRenameConversation(newTitle: string) {
		if (!sessionId) return;
		try {
			const response = await api.updateChatSession(sessionId, { name: newTitle });
			if (response.success) {
				recentSessions = recentSessions.map((s) => (s.id === sessionId ? { ...s, name: newTitle } : s));
			} else {
				logger.error('Failed to rename conversation:', response.message);
			}
		} catch (err) {
			logger.error('Failed to rename conversation:', err);
		}
	}

	/** The loaded transcript is a windowed tail — exporting it as-is once
	 * `hasEarlier` is true would silently drop everything before the window.
	 * Fetch the whole conversation (no `tail`) just for this export rather
	 * than changing what's loaded on screen. */
	async function handleExportTranscript() {
		let exportMessages = messages;
		if ($chatSession.hasEarlier && sessionId) {
			try {
				const response = await api.getChatSession(sessionId);
				if (response.success && response.data) {
					exportMessages = response.data.messages.map(mapPersistedMessage);
				}
			} catch (err) {
				logger.error('Failed to load the full conversation for export — exporting the loaded window instead:', err);
			}
		}
		const lines: string[] = [`# ${conversationTitle}`, ''];
		for (const message of exportMessages) {
			if (message.isSystem || (!message.content && !message.tool_executions?.length)) continue;
			const speaker = message.role === 'user' ? 'You' : 'PotionAI';
			const when = message.timestamp ? new Date(message.timestamp).toLocaleString() : '';
			lines.push(`## ${speaker}${when ? ` — ${when}` : ''}`, '', message.content || '_(no text content)_', '');
		}
		const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `${conversationTitle.replace(/[^a-z0-9]+/gi, '-').replace(/^-+|-+$/g, '').toLowerCase() || 'conversation'}.md`;
		a.click();
		URL.revokeObjectURL(url);
	}

	async function handleDeleteConversation() {
		if (!sessionId) return;
		const id = sessionId;
		try {
			const response = await api.deleteChatSession(id);
			if (!response.success) {
				chatSession.patch({ error: response.error || 'Failed to delete conversation' });
				return;
			}
		} catch (err) {
			logger.error('Failed to delete conversation:', err);
			chatSession.patch({ error: 'Failed to delete conversation' });
			return;
		}
		handleSessionDeleted(id);
	}

	function selectFormImage(entry: FormImageEntry) {
		selectedImageData = {
			path: entry.media.path,
			relative_path: entry.media.relative_path,
			url: entry.url,
			name: entry.media.name || entry.media.path.split('/').pop() || entry.media.path,
			type: 'image'
		};
	}

	// A pasted image reaches here from ChatChipInput via ChatInput's
	// `onPasteImage`, unconditionally - whether there's anywhere for it to go
	// is decided here, not upstream.
	async function handlePasteImage(file: File) {
		// No vision surface to receive it - nothing to attach, and no toast
		// either: an accidental image paste in a text-only mode should be a
		// silent no-op, not an error.
		if (!supportsVision) return;

		const blocked = blockedUploadMessage([file]);
		if (blocked) {
			chatSession.patch({ error: blocked });
			return;
		}

		try {
			const response = await api.uploadMedia(file);
			if (!response.success || !response.data) {
				throw new Error(response.message || 'Failed to upload pasted image');
			}
			// Same shape MediaLoaderField's own upload path builds - reused so a
			// pasted image is indistinguishable, downstream, from one picked
			// through the field itself.
			const mediaItem = buildUploadedMediaItem(response.data, file.name, 'image');
			handleMediaLoaderChange('vision_image', mediaItem);
		} catch (error) {
			logger.error('Failed to upload pasted image:', error);
			chatSession.patch({ error: refusalMessage(error) ?? 'Failed to attach the pasted image.' });
		}
	}

	function handleMediaLoaderChange(_fieldName: string, value: any) {
		const scrollPos = messagesContainerRef?.scrollTop || 0;

		if (value && typeof value === 'object' && value.path) {
			selectedImageData = value;
		} else {
			selectedImageData = null;
		}

		tick().then(() => {
			if (messagesContainerRef) {
				messagesContainerRef.scrollTop = scrollPos;
			}
		});
	}

	function scrollToBottom() {
		if (messagesContainerRef) jumpTo(messagesContainerRef, messagesContainerRef.scrollHeight);
	}

	// Both hosts gate this component behind a Svelte {#if}, so this container
	// (and its scrollTop) can be destroyed and recreated from scratch: the
	// global chat panel unmounts on every close and remounts fresh on every
	// reopen, and the mobile generate panel unmounts/remounts on an
	// isMobile flip. A brand-new node always starts at the browser's default
	// scrollTop of 0, and any parent that instead collapses this element via
	// display:none (or a zero-height ancestor) without unmounting it has the
	// same effect — the browser drops scrollTop and hands back 0 when it
	// reappears. preserveScrollAcrossHiding treats every case the same way:
	// any time this node is (re)created — a fresh mount, or the history view
	// swapping this element back in — start from "was hidden" so the first
	// ResizeObserver callback (which fires only after layout, so scrollHeight
	// is accurate) restores or pins scroll instead of leaving it at 0.
	let lastScrollTop = 0;
	let wasPinnedToBottom = true;
	let wasCollapsed = false;

	// The follow-along scroll used DURING a stream (turnController's
	// `scrollToBottomIfPinned`): only jumps while the reader is already
	// pinned to the bottom, so a reader who scrolled up to read earlier
	// messages is never yanked back down by content still arriving
	// elsewhere — `scrollToBottom` above stays the unconditional jump for
	// explicit actions (send, open session). Coalesced to at most once per
	// frame on its own (independent of turnController's own per-frame token
	// batching) so a burst of tool_start/status events in the same tick
	// still only forces one layout read.
	let pinnedScrollHandle: ReturnType<typeof scheduleFrame> | null = null;
	function scrollToBottomIfPinned() {
		if (!wasPinnedToBottom || pinnedScrollHandle !== null) return;
		pinnedScrollHandle = scheduleFrame(() => {
			pinnedScrollHandle = null;
			if (wasPinnedToBottom) scrollToBottom();
		});
	}

	function rememberScroll() {
		if (!messagesContainerRef || messagesContainerRef.clientHeight === 0) return;
		const { scrollTop, scrollHeight, clientHeight } = messagesContainerRef;
		lastScrollTop = scrollTop;
		wasPinnedToBottom = scrollHeight - scrollTop - clientHeight < 40;
	}

	// Programmatic positioning must never animate: the transcript's CSS
	// smooth-scrolling is for the reader's own wheel, while opening the panel
	// or loading a conversation has to land on the latest messages already
	// there, not visibly scroll down to them.
	function jumpTo(node: HTMLElement, top: number) {
		const previous = node.style.scrollBehavior;
		node.style.scrollBehavior = 'auto';
		node.scrollTop = top;
		node.style.scrollBehavior = previous;
	}

	function preserveScrollAcrossHiding(node: HTMLElement) {
		wasCollapsed = true;
		if (node.clientHeight > 0) jumpTo(node, node.scrollHeight);
		const observer = new ResizeObserver(() => {
			const collapsed = node.clientHeight === 0;
			if (!collapsed && wasCollapsed) {
				// Reading at the bottom is the resting state, and messages can
				// arrive while hidden, so being pinned wins over the old offset.
				jumpTo(node, wasPinnedToBottom ? node.scrollHeight : lastScrollTop);
			}
			wasCollapsed = collapsed;
		});
		observer.observe(node);
		// A reader at the bottom follows whatever the reply grows by — tokens,
		// the steps record, tool rows, images — without having to nudge the
		// scroll themselves; a reader who scrolled up is left alone.
		const content = node.firstElementChild;
		const follow = new ResizeObserver(() => {
			if (node.clientHeight > 0 && wasPinnedToBottom) jumpTo(node, node.scrollHeight);
		});
		if (content) follow.observe(content);
		return {
			destroy: () => {
				observer.disconnect();
				follow.disconnect();
			}
		};
	}

	function handleKeyDown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			handleSend();
		} else if (e.key === 'Escape') {
			handleClose();
		}
	}
</script>

<!-- History rail: a real column (mock's <aside class="history-rail">), always
     mounted — .chat-shell's own &.rail-collapsed rule (chat-concept.css)
     animates it to zero width instead of this unmounting it. -->
<ChatHistoryView onOpenSession={loadSession} onNewChat={handleNewSession} onSessionDeleted={handleSessionDeleted} />

<section class="conversation" class:memory-open={showMemoryPanel}>
	<ChatHeader
		{llmConfigs}
		{selectedConfigId}
		onSelectConfig={handleSelectConfig}
		onSelectMode={handleSelectMode}
		pageModeId={currentPageMode}
		onNewChat={handleNewSession}
		{currentContextSize}
		railCollapsed={historyRailCollapsed}
		onToggleRail={toggleHistoryRail}
		title={conversationTitle}
		subtitle={headerSubtitle}
		hasSession={!!sessionId}
		onRename={handleRenameConversation}
		onExportTranscript={handleExportTranscript}
		onDeleteConversation={handleDeleteConversation}
		onClose={onClose ? handleClose : undefined}
	/>

	{#if configsLoaded && llmConfigs.length === 0}
		<!-- No usable LLM config — a state the prototype never shows.
		     Non-admins get no pointer into Admin — they can't act on it. -->
		<div class="chat-shell-notice">
			<Icon name="chat-info" className="icon !h-[28px] !w-[28px] text-fg-subtle" strokeWidth={1.8} />
			{#if $authStore.user?.account_type === 'ADMIN'}
				<p>No enabled LLM configurations</p>
				<p>
					Enable or create one in <a href="/admin?tab=llm" style="color:rgb(var(--signal))"
						>Admin → LLM Configuration</a
					> to start chatting.
				</p>
			{:else}
				<p>Chat is currently unavailable</p>
				<p>You are not allowed to use any chat configuration right now. If you think this is a mistake, contact your administrator.</p>
			{/if}
		</div>
	{:else}
		<!-- Chat messages area -->
		<div class="messages" bind:this={messagesContainerRef} use:preserveScrollAcrossHiding on:scroll={rememberScroll}>
			<div class="messages-inner" class:hidden={messages.length === 0}>
				{#if $chatSession.hasEarlier}
					<div class="flex justify-center py-3" use:loadEarlierOnIntersect>
						<Button
							variant="ghost"
							size="sm"
							class="text-fg-muted"
							loading={$chatSession.loadingEarlier}
							onclick={loadEarlier}
						>
							Load earlier messages
						</Button>
					</div>
				{/if}
				{#each messages as message, idx (message.id ?? message.clientKey ?? idx)}
					{#if message.isStreaming && !message.content && !message.tool_executions?.length && !message.trace_steps?.length && !message.reasoning?.length && !message.notices?.length && idx === messages.length - 1}
						<!-- Nothing has happened for this turn yet (no content, no tool call, no
						     trace step) — a bare "thinking" placeholder covers that brief gap
						     for a tool called with no preceding narration. Once any of those
						     arrive this branch never renders again for the turn
						     (tool_executions/trace_steps only grow and content is never
						     blanked), so the assistant bubble mounts once and stays mounted —
						     content and tool rows render as updates inside it
						     (ChatMessage/ChatBehaviorTrace). -->
						<ChatThinkingBubble />
					{:else}
						<ChatMessage
							role={message.role}
							content={message.content}
							timestamp={message.timestamp}
							imageUrl={message.imageUrl || undefined}
							compact={false}
							isStreaming={message.isStreaming || false}
							isPartial={message.isPartial || false}
							onApplyAction={enableTools
								? (action, actionIndex) =>
										handleApplySegmentAction(action, message.id || '', actionIndex)
								: undefined}
							onUndoAction={(actionIndex) => handleUndoSegmentAction(message.id || '', actionIndex)}
							segmentTextFor={segmentTextForAction}
							listActionsDisabled={videoDirectorActive || !contextTab}
							batchPromptCount={contextTab ? promptCount(contextTab) : 0}
							applyActionHint={videoDirectorActive
								? "Applies to the Video Director's persistent Direction prompt"
								: undefined}
							onPromptFeedback={message.id
								? (data) => handlePromptFeedback(message.id!, data)
								: undefined}
							toolExecutions={message.tool_executions || []}
							traceSteps={message.trace_steps || []}
							reasoning={message.reasoning || []}
							notices={message.notices || []}
							sources={message.sources || []}
							sessionId={sessionId || ''}
							messageId={message.id || ''}
							metadata={message.metadata}
							parsedContent={message.parsed_content}
							variables={contextTab?.variables}
							variableRolls={contextTab?.variableRolls}
							promptResources={chatResources.specs}
							resourceFieldLabels={chatResources.fieldLabels}
							resourceFormValues={contextTab?.formData ?? {}}
							actionsLocked={isGenerating}
							{actionErrors}
							onResolveAction={message.id
								? (index, decision) => handleResolveAction(message.id!, index, decision)
								: undefined}
							onAskRedo={(index) => handleAskRedo(message.tool_executions?.[index])}
						/>
					{/if}
					<!-- Tab-switch divider: same idiom as a date divider, not a system
					     message. Client-side only (see contextDividers above); can land
					     after any message index, including the last one. -->
					{#each contextDividers.filter((d) => d.afterIndex === idx) as divider (divider.id)}
						<div class="context-switch-divider" data-testid="context-switch-divider">
							<div class="rule"></div>
							<span
								>Switched to {divider.tabName}{#if divider.presetLabel} · {divider.presetLabel}{/if}{#if divider.dims}
									· {divider.dims}{/if}</span
							>
							<div class="rule"></div>
						</div>
					{/each}
				{/each}

				{#if isGenerating && !messages.some((m) => m.isStreaming)}
					<ChatThinkingBubble />
				{/if}
			</div>

			<!-- Empty state: orb + prompt + three suggestion cards that hand
			     their prompt straight to the composer. -->
			<div class="empty-state" class:hidden={messages.length > 0}>
				<div class="empty-content">
					<span class="empty-orb"><Logo size={27} /></span>
					<h2>{intro.heading}</h2>
					<p>{intro.body}</p>
					<div class="suggestions">
						{#each starters as suggestion (suggestion.title)}
							<button
								type="button"
								class="suggestion"
								on:click={() => {
									userInput = suggestion.prompt;
									inputRef?.focus();
								}}
							>
								<Icon name={suggestion.icon} className="icon" strokeWidth={1.8} /><strong>{suggestion.title}</strong
								><span>{suggestion.sub}</span>
							</button>
						{/each}
					</div>
				</div>
			</div>
		</div>

		{#if pendingAction && !pendingCardVisible}
			<div class="chat-overlay-col">
				<div class="action-jump-row">
					<button type="button" class="action-jump" data-testid="action-jump" on:click={jumpToPendingAction}>
						<span class="action-jump-dot" aria-hidden="true"></span>1 action waiting for approval<span
							class="action-kbd"
							aria-hidden="true">↓</span
						>
					</button>
				</div>
			</div>
		{/if}
		{#if !pendingAction && pendingQuestionQueue.length > 0}
			<QuestionDock {messages} onAnswerQuestion={(text) => sendMessage(text)} />
		{/if}

		<!-- Error display -->
		{#if $chatSession.error}
			<div class="chat-overlay-col">
				<div class="chat-error-banner">
					<Icon name="chat-info" className="icon" strokeWidth={1.8} />
					{$chatSession.error}
				</div>
			</div>
		{/if}

		<!-- Pinned-to-another-tab warning: the only context state that needs its
		     own block with actions; the following/pinned-active states render as
		     the chip inside the composer's context row (see ChatInput's slot). -->
		{#if stripModel && stripModel.state === 'pinned-mismatch'}
			<div class="chat-overlay-col">
				<ChatContextStrip
					model={stripModel}
					flash={stripFlash}
					{allTabs}
					activeTabId={$activeTab?.id ?? null}
					{pinnedTabId}
					{tabPresetNames}
					onPinTab={savePinnedTab}
					onSwitchToPinned={() => {
						if (pinnedTabId) tabsStore.setActiveTab(pinnedTabId);
					}}
				/>
			</div>
		{/if}

		<!-- Input area: @resource chip editor + action row -->
		<ChatInput
			bind:this={inputRef}
			bind:value={userInput}
			bind:resources={userResources}
			mode={currentMode}
			formData={contextTab?.formData || {}}
			{loraSelections}
			disabled={isGenerating || llmConfigs.length === 0}
			actionPending={!!pendingAction}
			{isGenerating}
			{supportsVision}
			{formImageEntries}
			{selectedDurablePath}
			onSelectFormImage={selectFormImage}
			{selectedImageData}
			onMediaLoaderChange={(value) => handleMediaLoaderChange('vision_image', value)}
			{lastGeneratedImage}
			onAttachLastImage={attachLastGeneratedImage}
			onRemoveImage={removeSelectedImage}
			onSend={handleSend}
			onStop={handleStop}
			onKeydown={handleKeyDown}
			onPasteImage={handlePasteImage}
			{visibleTools}
			disabledTools={$chatSession.disabledTools}
			{enableTools}
			onToggleEnableTools={handleToggleEnableTools}
			onToggleTool={handleToggleTool}
			{myToolPreferences}
			onOpenToolPreferences={() => (showToolPreferencesPanel = true)}
			{alwaysAttachLastImage}
			onToggleAttachImage={toggleAttachLastImage}
			onOpenMemory={openMemoryPanel}
			memoryOpen={showMemoryPanel}
			{memoryNoteCount}
		>
			<svelte:fragment slot="context">
				{#if stripModel && stripModel.state !== 'pinned-mismatch'}
					<ChatContextStrip
						model={stripModel}
						flash={stripFlash}
						{allTabs}
						activeTabId={$activeTab?.id ?? null}
						{pinnedTabId}
						{tabPresetNames}
						onPinTab={savePinnedTab}
					/>
				{/if}
			</svelte:fragment>
		</ChatInput>
	{/if}

	<!-- Memory inspector: absolutely positioned inside .conversation (Seam 2),
	     not an overlay — opening it collapses the rail (see openMemoryPanel). -->
	{#if showMemoryPanel}
		<ChatMemoryPanel
			presetId={contextTab?.selectedPreset ?? null}
			formData={contextTab?.formData ?? {}}
			modeId={currentMode}
			modeLabel={resolveModeName(currentMode, $chatModes.modes)}
			sessionId={chatFormSessionId(contextTab, currentMode)}
			onClose={closeMemoryPanel}
			onCountChange={(n) => (memoryNoteCount = n)}
		/>
	{/if}
</section>

<!-- My Tools panel (slide-out overlay, persistent per-user tool opt-out) -->
{#if showToolPreferencesPanel}
	<ChatToolPreferencesPanel
		llmConfigId={selectedConfigId || null}
		onClose={() => (showToolPreferencesPanel = false)}
		onChanged={loadMyToolPreferences}
	/>
{/if}
