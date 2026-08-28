<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { onDestroy, tick } from 'svelte';
	import { browser } from '$app/environment';
	import { storage } from '$lib/utils/storage';
	import { api, type ChatSessionResponse, type ChatMessageResponse } from '$lib/services/api/index';
	import ChatMessage from '$lib/components/ChatMessage.svelte';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import { activeTab, tabsStore } from '$lib/stores/tabs';
	import { loraSelectionsForTab } from '$lib/stores/loraPickerSelections';
	import { page } from '$app/stores';
	import { chatSession } from '$lib/stores/chatSession';
	import { chatComposerDrafts } from '$lib/stores/chatComposerDrafts';
	import { chatModes, resolveModeForRoute, resolveModeName, toolsForMode } from '$lib/stores/chatModes';
	import ChatHeader from '$lib/components/chat/ChatHeader.svelte';
	import ChatMemoryPanel from '$lib/components/chat/ChatMemoryPanel.svelte';
	import ChatToolPreferencesPanel from '$lib/components/chat/ChatToolPreferencesPanel.svelte';
	import type { UserToolPreference } from '$lib/types/llm';
	import ChatHistoryView from '$lib/components/chat/ChatHistoryView.svelte';
	import { authStore } from '$lib/stores/auth';
	import ChatInput from '$lib/components/chat/ChatInput.svelte';
	import ApprovalDock from '$lib/components/chat/ApprovalDock.svelte';
	import ChatScopeBanner from '$lib/components/chat/ChatScopeBanner.svelte';
	import ChatContextStrip from '$lib/components/chat/ChatContextStrip.svelte';
	import { deriveContextStripModel, deriveTabSwitchDivider } from '$lib/chat/contextStrip';
	import { shouldShowScopeMismatch, type ScopeDismissal } from '$lib/utils/chatScopeMismatch';
	import ChatThinkingBubble from '$lib/components/chat/ChatThinkingBubble.svelte';
	import { deriveApprovalQueue } from '$lib/chat/approvalQueue';
	import { deriveQuestionQueue, dismissedQuestions } from '$lib/chat/questionQueue';
	import type {
		UnifiedChatMessageData as ChatMessageData,
		ResourceChipData
	} from '$lib/types/chat';
	import type { Segment } from '$lib/types/segments';
	import { buildVariablesSnapshot } from '$lib/utils/variableSnapshot';
	import { flattenRichSegments, isSegmentEnabled } from '$lib/utils/richSegments';
	import { applySegmentUpdate } from '$lib/utils/promptSegments';
	import { lastAppliedSegment } from '$lib/stores/lastAppliedSegment';
	import { appliedSegmentActions } from '$lib/stores/appliedSegmentActions';
	import { applyTitle } from '$lib/utils/chatStream';
	import {
		resolveDirectorCapabilities,
		normalizeDirectorValue,
		applyDirectorSegmentPrompt
	} from '$lib/utils/videoDirector';
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
	import { loadHistoryRailCollapsed, saveHistoryRailCollapsed } from '$lib/utils/chatHistoryRail';

	// Props
	export let onClose: (() => void) | undefined = undefined;

	// LocalStorage keys (the active session id is a single global key, see chatConfig.ts)
	const STORAGE_KEY_CONFIG = 'unified-ai-chat-config-id';
	const STORAGE_KEY_ATTACH_IMAGE = 'unified-ai-chat-attach-image';
	const STORAGE_KEY_PINNED_TAB = 'unified-ai-chat-pinned-tab';
	const STORAGE_KEY_ENABLE_TOOLS = 'unified-ai-chat-enable-tools';

	// Conversation state lives in the chatSession store (survives panel close)
	$: sessionId = $chatSession.sessionId;
	$: messages = $chatSession.messages;
	$: currentMode = $chatSession.mode;
	$: isGenerating = $chatSession.isGenerating;
	$: pendingApprovalQueue = deriveApprovalQueue(messages);
	$: pendingQuestionQueue = deriveQuestionQueue(messages, $dismissedQuestions);

	// Route only decides the mode of a NEW conversation. When the active
	// session's mode differs from what the current route would resolve to,
	// ChatScopeBanner explains the mismatch instead of silently swapping chats.
	$: routeMode = resolveModeForRoute($page.url.pathname, $chatModes.modes);
	let dismissedScopeMismatch: ScopeDismissal | null = null;
	$: showScopeMismatch = shouldShowScopeMismatch(
		currentMode,
		routeMode,
		sessionId,
		dismissedScopeMismatch
	);
	$: sessionModeName = resolveModeName(currentMode, $chatModes.modes);
	$: routeModeName = resolveModeName(routeMode, $chatModes.modes);

	function dismissScopeMismatch() {
		dismissedScopeMismatch = { sessionId, routeMode };
	}

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

	let showImagePanel = false;
	let showMemoryPanel = false;
	let showToolPreferencesPanel = false;

	// History rail (left of the conversation, not a covering view) — persists
	// only the collapsed/expanded boolean, read once on mount.
	let historyRailCollapsed = loadHistoryRailCollapsed();
	function toggleHistoryRail() {
		historyRailCollapsed = !historyRailCollapsed;
		saveHistoryRailCollapsed(historyRailCollapsed);
	}
	// null = not yet loaded (the Tools popover shows every mode tool, same as
	// before this feature existed); once loaded, tools this user can't see at
	// all (admin-disabled) are filtered out of that popover.
	let myToolPreferences: UserToolPreference[] | null = null;

	// Configuration data
	let llmConfigs: any[] = [];
	let selectedConfigId = '';
	let enableTools = browser ? storage.get(STORAGE_KEY_ENABLE_TOOLS) !== 'false' : true;
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
	let alwaysAttachLastImage = storage.get(STORAGE_KEY_ATTACH_IMAGE) === 'true';
	let lastAutoAttachedUrl: string | null = null;
	function toggleAttachLastImage() {
		alwaysAttachLastImage = !alwaysAttachLastImage;
		storage.set(STORAGE_KEY_ATTACH_IMAGE, alwaysAttachLastImage ? 'true' : 'false');
	}

	// Tab context pinning
	let pinnedTabId: string | null = storage.get(STORAGE_KEY_PINNED_TAB);

	function savePinnedTab(id: string | null) {
		pinnedTabId = id;
		if (id) {
			storage.set(STORAGE_KEY_PINNED_TAB, id);
		} else {
			storage.remove(STORAGE_KEY_PINNED_TAB);
		}
	}

	// All available tabs for pin dropdown
	$: allTabs = $tabsStore.tabs || [];

	// Safety: clear stale pinnedTabId when it no longer references a valid tab
	$: if (pinnedTabId && allTabs.length > 0 && !allTabs.find((t: any) => t.id === pinnedTabId)) {
		savePinnedTab(null);
	}

	// Resolved context tab: pinned or active
	$: contextTab = pinnedTabId
		? (allTabs.find((t: any) => t.id === pinnedTabId) || $activeTab)
		: $activeTab;

	// Preset display names for the context strip / pin picker, resolved once
	// (same lookup ChatMemoryPanel does per-preset via listPresets().find) and
	// cached for every preset id at once since both surfaces need it for
	// every open tab, not just the current one.
	let presetNamesCache: Record<string, string> = {};
	async function loadPresetNames() {
		try {
			const response = await api.listPresets();
			if (response.success) {
				const map: Record<string, string> = {};
				for (const p of response.data || []) map[p.id] = p.name;
				presetNamesCache = map;
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
		activeTab: $activeTab,
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
	$: videoDirectorCapsKey = `${contextTab?.selectedPreset || ''}:${contextTab?.selectedMode || ''}:${JSON.stringify(videoDirectorCapsRaw ?? null)}`;
	$: videoDirectorCaps = (() => {
		if (videoDirectorCapsCache && videoDirectorCapsCache.key === videoDirectorCapsKey) {
			return videoDirectorCapsCache.caps;
		}
		const caps = resolveDirectorCapabilities(videoDirectorCapsRaw, contextTab?.selectedMode ?? null);
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

	// Token usage tracking
	$: totalTokensUsed = messages.reduce((sum, m) => sum + (m.tokens_used || 0), 0);
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
		storage.set(STORAGE_KEY_ENABLE_TOOLS, enableTools ? 'true' : 'false');
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
			const resolved = resolveModeForRoute($page.url.pathname, $chatModes.modes);
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
		const resolved = resolveModeForRoute($page.url.pathname, $chatModes.modes);
		chatComposerDrafts.clear(sessionId);
		chatSession.newConversation(resolved);
		saveActiveSessionId('');
		applyStoredDisabledToolsForMode(resolved);
		loadRecentSessions();
	}

	async function startNewSession() {
		try {
			// Subtractive tool filter: omit enabled_tools when everything is on;
			// send the reduced list when the user unticked tools; [] disables all.
			const disabled = $chatSession.disabledTools;
			let enabledToolsPayload: string[] | undefined;
			if (!enableTools) {
				enabledToolsPayload = [];
			} else if (disabled.length > 0) {
				enabledToolsPayload = visibleTools
					.map((t) => t.name)
					.filter((name) => !disabled.includes(name));
			}

			const response = await api.createChatSession({
				llm_config_id: selectedConfigId || undefined,
				mode: $chatSession.mode,
				enabled_tools: enabledToolsPayload
			});

			if (response.success && response.data) {
				chatSession.patch({
					sessionId: response.data.id
				});
				saveCurrentSessionId();
				recentSessions = [response.data, ...recentSessions];
			} else {
				chatSession.patch({ error: 'Failed to create chat session' });
			}
		} catch (err) {
			logger.error('Failed to create session:', err);
			chatSession.patch({ error: 'Failed to create chat session' });
		}
	}

	async function loadSession(id: string) {
		if (loadingSessionId === id) return;
		if ($chatSession.sessionId === id && $chatSession.messages.length > 0) {
			return;
		}

		const requestId = ++sessionLoadRequestId;
		loadingSessionId = id;
		try {
			const response = await api.getChatSession(id);
			if (destroyed || requestId !== sessionLoadRequestId) return;

			if (response.success && response.data) {
				const loadedMessages = response.data.messages.map((msg: ChatMessageResponse) => {
					const metadata = (msg as any).metadata || {};
					const toolExecs = metadata.tool_executions || (msg as any).tool_executions || [];
					return {
						id: msg.id,
						role: msg.role,
						content: msg.content,
						timestamp: msg.created_at ? new Date(msg.created_at).getTime() : Date.now(),
						imageUrl: metadata.image_url || null,
						tokens_used: msg.tokens_used,
						prompt_tokens: msg.prompt_tokens,
						completion_tokens: msg.completion_tokens,
						tool_executions: toolExecs,
						sources: toolExecs.flatMap((te: any) => te.result?.sources || []),
						metadata
					};
				});

				chatSession.loadedSession(
					{
						id: response.data.id,
						mode: response.data.mode
					},
					loadedMessages
				);
				applyStoredDisabledToolsForMode(response.data.mode);
				saveCurrentSessionId();

				if (response.data.llm_config_id) {
					selectedConfigId = response.data.llm_config_id;
				}

				await tick();
				scrollToBottom();

				// A turn was still streaming when we loaded — resume it so a
				// mid-response reload doesn't dead-end on a lost reply.
				if (response.data.active_turn?.status === 'running') {
					void reattachToTurn(response.data.id);
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

	onDestroy(() => {
		destroyed = true;
		sessionsRequestId += 1;
		sessionLoadRequestId += 1;
		if (stripFlashTimer) clearTimeout(stripFlashTimer);
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

	// One SSE event handler, shared by the live send stream and the reattach
	// stream so a reloaded, resumed turn drives the exact same reducers. Closes
	// over its own token accumulator; a reattach replays tokens from the start,
	// so accumulating from '' reconstructs the same content the live path built.
	function createStreamEventHandler() {
		let streamedContent = '';
		return (event: { type: string; data: any }) => {
			if (event.type === 'message_created') {
				// no-op
			} else if (event.type === 'token') {
				streamedContent += event.data.content;
				chatSession.applyStreamEvent(event, { accumulated: streamedContent });
				scrollToBottom();
			} else if (event.type === 'tool_start') {
				chatSession.applyStreamEvent(event);
				scrollToBottom();
			} else if (event.type === 'tool_end') {
				chatSession.applyStreamEvent(event);
			} else if (event.type === 'status') {
				chatSession.applyStreamEvent(event);
				scrollToBottom();
			} else if (event.type === 'done') {
				chatSession.applyStreamEvent(event);
				selectedImageData = null;
			} else if (event.type === 'title') {
				// Async LLM-generated session title (arrives after done)
				const titled = event.data || {};
				if (titled.session_id && titled.name) {
					recentSessions = applyTitle(recentSessions, titled.session_id, titled.name);
				}
			} else if (event.type === 'generation_cancelled') {
				// The turn was stopped; drop the streaming placeholder like an error.
				chatSession.applyStreamEvent({ type: 'error', data: {} });
			} else if (event.type === 'error') {
				chatSession.patch({ error: event.data.message || 'Streaming error' });
				chatSession.applyStreamEvent(event);
			}
			// 'no_active_turn' needs no handling — the trailing empty placeholder
			// is cleaned up when the reattach stream ends.
		};
	}

	// Reattach to a turn still running on the backend (page reload mid-response).
	// The persisted messages already include the user message; we add a streaming
	// assistant placeholder and replay the turn's events into it.
	async function reattachToTurn(sessionId: string) {
		chatSession.patch({ isGenerating: true, error: '' });
		chatSession.addMessage({
			role: 'assistant',
			content: '',
			timestamp: Date.now(),
			isStreaming: true
		});
		await tick();
		scrollToBottom();

		try {
			await api.reattachChatMessageStream(sessionId, createStreamEventHandler());
		} catch (err) {
			logger.error('Failed to reattach to in-flight turn:', err);
		} finally {
			// Drop a still-empty placeholder (e.g. the turn had already finished
			// and was evicted, so nothing was replayed).
			chatSession.updateMessages((msgs) =>
				msgs.filter(
					(m, idx) =>
						!(
							idx === msgs.length - 1 &&
							m.role === 'assistant' &&
							m.isStreaming &&
							!m.content &&
							!m.tool_executions?.length &&
							!m.trace_steps?.length
						)
				)
			);
			chatSession.patch({ isGenerating: false });
			await tick();
			scrollToBottom();
		}
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

	/**
	 * Send `instruction` as a user turn through the same streaming pipeline
	 * `handleSend` drives — factored out so a programmatic send (answering a
	 * docked question) goes through the exact request/response handling
	 * instead of faking composer input state. `handleSend` owns the composer
	 * (userInput/userResources, the `/command` gate); this owns everything
	 * downstream of "an instruction is ready to go out".
	 */
	async function sendMessage(
		instruction: string,
		options: { resourceRefs?: { uri: string }[]; attachedResources?: ResourceChipData[] } = {}
	) {
		if (!instruction.trim() || $chatSession.isGenerating) return;
		if (!selectedConfigId) return;
		const resourceRefs = options.resourceRefs || [];
		const attachedResources = options.attachedResources || [];

		chatSession.patch({ error: '', isGenerating: true });

		try {
			// Create session if not exists
			if (!$chatSession.sessionId) {
				await startNewSession();
				if (!$chatSession.sessionId) {
					chatSession.patch({ isGenerating: false });
					return;
				}
			}

			// Auto-attach last generated image only when it changes
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

			// Add user message to UI immediately (include image URL if attached).
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
			chatSession.addMessage(tempUserMessage);

			await tick();
			scrollToBottom();

			// Build context_metadata so LLM tools have access to form state.
			// In Video Director mode "segment #N" means a shot (get_video_director),
			// not a global prompt segment, so the global segments never go out as
			// the chat's `segments` context — get_current_segments/PROMPT STATE stay
			// unavailable and can't compete with that meaning. tab.videoDirector.
			// global_prompt_segments is still read directly by handleApplySegmentAction
			// (old transcripts can still apply an update_segment card).
			const tab = contextTab;
			const activeSegments: Segment[] = videoDirectorActive ? [] : tab?.promptSegments || [];
			const negativeSegments: Segment[] = videoDirectorActive
				? []
				: tab?.negativePromptSegments || [];
			const mapSegment = (seg: Segment, i: number, negative: boolean) => ({
				index: i,
				id: seg.id,
				content: seg.content,
				name: seg.name || seg.title || null,
				type: seg.type || 'content',
				enabled: isSegmentEnabled(seg),
				...(seg.template ? { template: seg.template } : {}),
				...(negative ? { negative: true } : {})
			});
			const contextMetadata: Record<string, any> = {
				segments: [
					...activeSegments.map((seg, i) => mapSegment(seg, i, false)),
					...negativeSegments.map((seg, i) => mapSegment(seg, activeSegments.length + i, true))
				],
				form_state: {
					preset: tab?.selectedPreset || null,
					mode: tab?.selectedMode || null,
					variant: tab?.selectedVariant || null,
					form_data: tab?.formData || {},
					variables: buildVariablesSnapshot(tab?.variables, tab?.variableRolls),
					video_director: videoDirectorActive
						? {
								active: true,
								doc: normalizeDirectorValue(tab?.videoDirector, videoDirectorCaps!),
								capabilities: videoDirectorCapsRaw
							}
						: { active: false, doc: null, capabilities: null },
					music_director: musicDirectorActive
						? {
								active: true,
								doc: normalizeMusicDirectorValue(tab?.musicDirector, musicDirectorCaps!),
								capabilities: musicDirectorCapsRaw
							}
						: { active: false, doc: null, capabilities: null }
				}
			};
			// Include image URL so backend can store it for chat history display
			if (effectiveImageData?.url) {
				contextMetadata.image_url = effectiveImageData.url;
			}

			// Add streaming assistant placeholder
			chatSession.addMessage({
				role: 'assistant' as const,
				content: '',
				timestamp: Date.now(),
				isStreaming: true
			});
			await tick();
			scrollToBottom();

			try {
				await api.sendChatMessageStream(
					$chatSession.sessionId!,
					{
						content: instruction,
						imageData: effectiveImageData?.relative_path || effectiveImageData?.path || undefined,
						contextMetadata,
						resources: resourceRefs
					},
					createStreamEventHandler()
				);
			} catch (err: any) {
				logger.error('Stream error, falling back to non-streaming:', err);
				// Remove streaming placeholder
				chatSession.updateMessages((msgs) =>
					msgs.filter(
						(m, idx) => !(idx === msgs.length - 1 && m.role === 'assistant' && m.isStreaming)
					)
				);

				// Fallback to non-streaming
				try {
					const response = await api.sendChatMessage($chatSession.sessionId!, {
						content: instruction,
						imageData: effectiveImageData?.relative_path || effectiveImageData?.path || undefined,
						timeoutSeconds: selectedConfig?.timeout,
						contextMetadata,
						resources: resourceRefs
					});

					if (response.success && response.data) {
						const userMsg = response.data.user_message;
						const assistantMsg = response.data.assistant_message;
						chatSession.updateMessages((msgs) => {
							const updatedMessages = msgs.map((m, idx) => {
								if (idx === msgs.length - 1 && m.role === 'user') {
									return {
										id: userMsg.id,
										role: userMsg.role as 'user',
										content: instruction,
										timestamp: userMsg.created_at
											? new Date(userMsg.created_at).getTime()
											: Date.now(),
										metadata: (userMsg as any).metadata || m.metadata
									};
								}
								return m;
							});
							return [
								...updatedMessages,
								{
									id: assistantMsg.id,
									role: assistantMsg.role as 'assistant',
									content: assistantMsg.content,
									timestamp: assistantMsg.created_at
										? new Date(assistantMsg.created_at).getTime()
										: Date.now(),
									tokens_used: assistantMsg.tokens_used,
									prompt_tokens: assistantMsg.prompt_tokens,
									completion_tokens: assistantMsg.completion_tokens,
									tool_executions:
										(assistantMsg as any).metadata?.tool_executions ||
										(assistantMsg as any).tool_executions ||
										[],
									metadata: (assistantMsg as any).metadata || undefined
								}
							];
						});
						selectedImageData = null;
					} else {
						chatSession.patch({ error: response.error || 'Failed to send message' });
					}
				} catch (fallbackErr: any) {
					chatSession.patch({ error: fallbackErr.message || 'Failed to send message' });
				}
			}
		} catch (err: any) {
			logger.error('Error sending message:', err);
			chatSession.patch({ error: err.message || 'Failed to send message' });
		} finally {
			chatSession.patch({ isGenerating: false });
			await tick();
			scrollToBottom();
			inputRef?.focus();
		}
	}

	async function handleApplySegmentAction(
		action: {
			type: string;
			segmentIndex: number;
			segmentId: string;
			content: string;
		},
		messageId: string,
		actionIndex: number
	) {
		const tab = contextTab;
		if (!tab) return;

		if (action.type === 'update_director_segment') {
			if (!videoDirectorActive || !videoDirectorCaps) return;
			const doc = normalizeDirectorValue(tab.videoDirector, videoDirectorCaps);
			// Resolve id-first/index-fallback against the SAME pre-apply list
			// applyDirectorSegmentPrompt targets, so the applied-marker lands on
			// the segment it actually wrote to.
			const list = videoDirectorCaps.segmentRouting ? doc.chain.segments : doc.timeline.segments;
			const byId = list.findIndex((s) => s.id === action.segmentId);
			const idx = byId !== -1 ? byId : action.segmentIndex >= 0 && action.segmentIndex < list.length ? action.segmentIndex : -1;
			const next = applyDirectorSegmentPrompt(doc, videoDirectorCaps, action);
			if (!next || idx === -1) return;
			tabsStore.updateTab(tab.id, { videoDirector: next });
			appliedSegmentActions.set(list[idx].id, messageId, actionIndex);
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

		const result = await applySegmentUpdate(sourceSegments, action);
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

	function handleToolApprovalResolved(data: {
		messageId: string;
		index: number;
		approved: boolean;
		updatedExecution: import('$lib/types/chat').ToolExecution;
		assistantMessage: any | null;
	}) {
		// Reflect the resolved execution on the originating message.
		chatSession.updateMessages((msgs) =>
			msgs.map((m) => {
				if (m.id !== data.messageId) return m;
				const execs = (m.tool_executions || []).map((te, i) =>
					i === data.index ? data.updatedExecution : te
				);
				return { ...m, tool_executions: execs };
			})
		);

		// An approved tool's result may carry an action the frontend must apply
		// (form field changes, Video Director ops, a prompt-relay timeline) —
		// same subtractive gate as every other AI-initiated mutation (enableTools).
		if (data.approved && enableTools) {
			try {
				const resultData = JSON.parse(data.updatedExecution.result?.data || '');
				if (resultData.action === 'apply_form_changes') {
					handleFormChangesApplied(resultData.applied_changes);
				}
				if (resultData.action === 'apply_music_director_ops') {
					handleMusicDirectorApplied(resultData.operations);
				}
				if (resultData.action === 'set_prompt_relay') {
					handlePromptRelaySet(resultData);
				}
				if (resultData.action === 'apply_segment_updates') {
					handleSegmentUpdatesApplied(resultData.updates);
				}
			} catch {
				/* result not JSON — nothing to apply */
			}
		}

		// Append the assistant's continuation (what it did / that it declined).
		const am = data.assistantMessage;
		if (am) {
			const metadata = am.metadata || {};
			const toolExecs = metadata.tool_executions || am.tool_executions || [];
			const hasContent = !!(am.content && am.content.trim());
			if (!hasContent && toolExecs.length === 0) {
				// Never render a dead-end blank bubble; the backend narrates
				// approve/deny outcomes deterministically, so this only guards
				// against a malformed response slipping through.
				return;
			}
			chatSession.addMessage({
				id: am.id,
				role: am.role || 'assistant',
				content: am.content || '',
				timestamp: am.created_at ? new Date(am.created_at).getTime() : Date.now(),
				tokens_used: am.tokens_used,
				prompt_tokens: am.prompt_tokens,
				completion_tokens: am.completion_tokens,
				tool_executions: toolExecs,
				sources: toolExecs.flatMap((te: any) => te.result?.sources || []),
				metadata
			});
			tick().then(scrollToBottom);
		}
	}

	async function handleSegmentUpdatesApplied(
		updates: Array<{ segment_id: string; segment_index: number; content: string }>
	) {
		const tab = contextTab;
		if (!tab || !updates || updates.length === 0) return;

		let segments: Segment[] = tab.promptSegments || [];
		let lastAppliedId: string | null = null;
		for (const update of updates) {
			const result = await applySegmentUpdate(segments, {
				segmentId: update.segment_id,
				segmentIndex: update.segment_index,
				content: update.content
			});
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
			const resolved = resolveModeForRoute($page.url.pathname, $chatModes.modes);
			chatSession.newConversation(resolved);
			applyStoredDisabledToolsForMode(resolved);
			saveActiveSessionId('');
		}
	}

	function selectFormImage(entry: FormImageEntry) {
		selectedImageData = {
			path: entry.media.path,
			relative_path: entry.media.relative_path,
			url: entry.url,
			name: entry.media.name || entry.media.path.split('/').pop() || entry.media.path,
			type: 'image'
		};
		showImagePanel = true;
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
		if (messagesContainerRef) {
			messagesContainerRef.scrollTop = messagesContainerRef.scrollHeight;
		}
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

	function rememberScroll() {
		if (!messagesContainerRef || messagesContainerRef.clientHeight === 0) return;
		const { scrollTop, scrollHeight, clientHeight } = messagesContainerRef;
		lastScrollTop = scrollTop;
		wasPinnedToBottom = scrollHeight - scrollTop - clientHeight < 40;
	}

	function preserveScrollAcrossHiding(node: HTMLElement) {
		wasCollapsed = true;
		const observer = new ResizeObserver(() => {
			const collapsed = node.clientHeight === 0;
			if (!collapsed && wasCollapsed) {
				// Reading at the bottom is the resting state, and messages can
				// arrive while hidden, so being pinned wins over the old offset.
				node.scrollTop = wasPinnedToBottom ? node.scrollHeight : lastScrollTop;
			}
			wasCollapsed = collapsed;
		});
		observer.observe(node);
		return { destroy: () => observer.disconnect() };
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

<div class="flex flex-col h-full min-h-0 flex-1 bg-canvas">
	<!-- Header bar: session, model, mode, toggles, close -->
	<ChatHeader
		{llmConfigs}
		{selectedConfigId}
		onSelectConfig={handleSelectConfig}
		onSelectMode={handleSelectMode}
		{supportsVision}
		hasImageAttached={!!selectedImageData}
		{totalTokensUsed}
		{currentContextSize}
		onClose={onClose ? handleClose : undefined}
	>
		<svelte:fragment slot="leading">
			<!-- History rail toggle + new chat -->
			<button
				type="button"
				title="Conversation history"
				aria-pressed={!historyRailCollapsed}
				class="p-1.5 rounded text-xs transition-colors {!historyRailCollapsed ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:text-fg-muted hover:bg-surface-2'}"
				on:click={toggleHistoryRail}
			>
				<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
				</svg>
			</button>
			<button
				type="button"
				title="New chat"
				class="p-1.5 rounded text-xs text-fg-muted hover:text-fg-muted hover:bg-surface-2 transition-colors"
				on:click={handleNewSession}
			>
				<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
				</svg>
			</button>
			<div class="w-px h-4 bg-surface-2"></div>
		</svelte:fragment>
	</ChatHeader>

	{#if configsLoaded && llmConfigs.length === 0}
		<!-- No usable LLM config: replaces the whole chat surface, rail included.
		     Non-admins get no pointer into Admin — they can't act on it. -->
		<div class="flex-1 min-h-0 flex flex-col items-center justify-center gap-3 px-6 text-center">
			<div class="w-10 h-10 inline-flex items-center justify-center rounded-lg bg-surface-2 border border-line-strong text-fg-subtle">
				<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 9v2m0 4h.01M5.062 19h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
				</svg>
			</div>
			{#if $authStore.user?.account_type === 'ADMIN'}
				<p class="text-sm font-semibold text-fg">No enabled LLM configurations</p>
				<p class="max-w-xs text-sm text-fg-muted">
					Enable or create one in <a href="/admin?tab=llm" class="text-signal hover:underline">Admin → LLM Configuration</a> to start chatting.
				</p>
			{:else}
				<p class="text-sm font-semibold text-fg">Chat is currently unavailable</p>
				<p class="max-w-xs text-sm text-fg-muted">
					You are not allowed to use any chat configuration right now. If you think this is a mistake, contact your administrator.
				</p>
			{/if}
		</div>
	{:else}
	<div class="flex flex-1 min-h-0 relative">
		{#if !historyRailCollapsed}
			<!-- In flow on md+; overlays the conversation from the left below md so
			     the composer never gets squeezed. -->
			<div class="absolute inset-y-0 left-0 z-20 w-60 max-w-[80vw] flex-shrink-0 flex flex-col min-h-0 overflow-y-auto bg-surface-1 border-r border-line shadow-floating md:static md:z-auto md:shadow-none">
				<ChatHistoryView
					compact
					onOpenSession={loadSession}
					onNewChat={handleNewSession}
					onSessionDeleted={handleSessionDeleted}
				/>
			</div>
		{/if}

		<div class="flex flex-col flex-1 min-w-0 min-h-0">
			<!-- Chat messages area -->
			<div
				bind:this={messagesContainerRef}
				use:preserveScrollAcrossHiding
				on:scroll={rememberScroll}
				class="flex-1 overflow-y-auto p-3 md:p-5 space-y-3 md:space-y-5 min-h-0 scrollbar-thin scrollbar-thumb-[rgb(var(--line-strong))] scrollbar-track-transparent hover:scrollbar-thumb-[rgb(var(--line-hover))]"
			>
				{#if messages.length === 0}
					<div class="dot-grid flex flex-col items-center justify-center h-full min-h-[150px] md:min-h-[300px] text-center px-4 md:px-6">
						<div class="mb-3 md:mb-6">
							<div class="w-12 h-12 md:w-16 md:h-16 rounded-lg bg-surface-1 border border-line flex items-center justify-center">
								<svg class="w-6 h-6 md:w-8 md:h-8 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
								</svg>
							</div>
						</div>
						<h3 class="text-base md:text-xl font-semibold text-fg-muted mb-1 md:mb-2">Chat with AI</h3>
						<p class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-3 md:mb-6 max-w-sm">Ask questions, get creative ideas, or explore what's possible</p>
						<div class="flex flex-wrap justify-center gap-2">
							<button
								class="px-3 py-1.5 text-xs font-medium text-fg-subtle bg-surface-1 border border-line rounded hover:text-fg-muted hover:border-signal transition-colors"
								on:click={() => (userInput = 'What styles work best for portraits?')}
							>
								Portrait styles
							</button>
							<button
								class="px-3 py-1.5 text-xs font-medium text-fg-subtle bg-surface-1 border border-line rounded hover:text-fg-muted hover:border-signal transition-colors"
								on:click={() => (userInput = 'How can I improve image quality?')}
							>
								Improve quality
							</button>
							<button
								class="px-3 py-1.5 text-xs font-medium text-fg-subtle bg-surface-1 border border-line rounded hover:text-fg-muted hover:border-signal transition-colors"
								on:click={() => (userInput = 'Suggest a creative prompt idea')}
							>
								Creative ideas
							</button>
						</div>
					</div>
				{:else}
					{#each messages as message, idx}
						{#if message.isStreaming && !message.content && !message.tool_executions?.length && !message.trace_steps?.length && idx === messages.length - 1}
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
								onApplyAction={enableTools
									? (action, actionIndex) =>
											handleApplySegmentAction(action, message.id || '', actionIndex)
									: undefined}
							applyActionHint={videoDirectorActive
								? "Applies to the Video Director's persistent Direction prompt"
								: undefined}
								onPromptFeedback={message.id
									? (data) => handlePromptFeedback(message.id!, data)
									: undefined}
								toolExecutions={message.tool_executions || []}
								traceSteps={message.trace_steps || []}
								sources={message.sources || []}
								sessionId={sessionId || ''}
								messageId={message.id || ''}
								metadata={message.metadata}
								parsedContent={message.parsed_content}
								variables={contextTab?.variables}
								variableRolls={contextTab?.variableRolls}
							/>
						{/if}
							<!-- Tab-switch divider: same idiom as a date divider, not a system
							     message. Client-side only (see contextDividers above); can land
							     after any message index, including the last one. -->
							{#each contextDividers.filter((d) => d.afterIndex === idx) as divider (divider.id)}
								<div class="flex items-center gap-2 py-0.5" data-testid="context-switch-divider">
									<div class="flex-1 h-px bg-line"></div>
									<div class="flex items-center gap-1.5 flex-shrink-0">
										<svg class="w-2.5 h-2.5 text-fg-subtle" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" d="M8 7h12m0 0l-4-4m4 4l-4 4M16 17H4m0 0l4 4m-4-4l4-4" />
										</svg>
										<span class="font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle whitespace-nowrap">
											Switched to {divider.tabName}{#if divider.presetLabel} · {divider.presetLabel}{/if}{#if divider.dims} · {divider.dims}{/if}
										</span>
									</div>
									<div class="flex-1 h-px bg-line"></div>
								</div>
							{/each}
					{/each}
				{/if}

				{#if isGenerating && !messages.some((m) => m.isStreaming)}
					<ChatThinkingBubble />
				{/if}
			</div>

			<!-- Approval/question dock: docked above the composer whenever a tool execution is
			     pending approval or the latest reply came with docked questions. Approvals rank
			     first inside the dock itself (they gate side effects); questions are optional. -->
			{#if pendingApprovalQueue.length > 0 || pendingQuestionQueue.length > 0}
				<ApprovalDock
					{messages}
					sessionId={sessionId || ''}
					onResolved={handleToolApprovalResolved}
					onAnswerQuestion={(text) => sendMessage(text)}
				/>
			{/if}

			<!-- Scope mismatch notice: docked above the composer when the active session's mode
			     doesn't match the route we're currently on -->
			{#if showScopeMismatch}
				<ChatScopeBanner
					{sessionModeName}
					{routeModeName}
					onStartNew={handleNewSession}
					onDismiss={dismissScopeMismatch}
				/>
			{/if}

			<!-- Error display -->
			{#if $chatSession.error}
				<div class="flex-shrink-0 px-3 md:px-4 pb-2 md:pb-3">
					<div class="bg-surface-1 border border-danger/25 rounded-lg p-3">
						<div class="flex items-center gap-2 text-sm text-danger">
							<svg class="w-5 h-5 text-danger flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
							</svg>
							{$chatSession.error}
						</div>
					</div>
				</div>
			{/if}

			<!-- Vision Image Upload (collapsible, toggled by image button) -->
			{#if supportsVision && showImagePanel}
				<div class="flex-shrink-0 px-3 pb-2 border-t border-line pt-2">
					{#if formImageEntries.length > 0}
						<div class="mb-2">
							<div class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle mb-1">
								From current form
							</div>
							<div class="flex gap-1.5 overflow-x-auto pb-1">
								{#each formImageEntries as entry (entry.key)}
									<button
										type="button"
										class="flex-shrink-0 w-14 h-14 rounded border overflow-hidden transition-colors {(entry
											.media.relative_path || entry.media.path) === selectedDurablePath
											? 'border-signal ring-2 ring-signal/50'
											: 'border-line hover:border-line-hover'}"
										title={entry.label}
										aria-label={entry.label}
										on:click={() => selectFormImage(entry)}
									>
										<img src={entry.url} alt={entry.label} class="w-full h-full object-cover" />
									</button>
								{/each}
							</div>
						</div>
					{/if}
					<MediaLoaderField
						name="vision_image"
						value={selectedImageData}
						onChange={handleMediaLoaderChange}
						config={{ title: 'Reference Image', accept: 'image/*' }}
						compact={true}
					/>
				</div>
			{/if}

			<!-- Context strip: states which tab the chat is reading, always visible
			     while the composer renders. Docked closest to the composer, below
			     the occasional Approval/Scope banners above. -->
			{#if stripModel}
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
			{/if}

			<!-- Input area: @resource chip editor + action row -->
			<ChatInput
				bind:this={inputRef}
				bind:value={userInput}
				bind:resources={userResources}
				mode={currentMode}
				formData={contextTab?.formData || {}}
				{loraSelections}
				disabled={isGenerating || llmConfigs.length === 0 || pendingApprovalQueue.length > 0}
				approvalsPending={pendingApprovalQueue.length > 0}
				{isGenerating}
				{supportsVision}
				imagePanelActive={showImagePanel || !!selectedImageData}
				onSend={handleSend}
				onStop={handleStop}
				onToggleImagePanel={() => (showImagePanel = !showImagePanel)}
				onKeydown={handleKeyDown}
				{visibleTools}
				disabledTools={$chatSession.disabledTools}
				{enableTools}
				onToggleEnableTools={handleToggleEnableTools}
				onToggleTool={handleToggleTool}
				{myToolPreferences}
				onOpenToolPreferences={() => (showToolPreferencesPanel = true)}
				{alwaysAttachLastImage}
				onToggleAttachImage={toggleAttachLastImage}
				onOpenMemory={() => (showMemoryPanel = true)}
				memoryOpen={showMemoryPanel}
				{pinnedTabId}
				contextTabId={contextTab?.id ?? ''}
				contextTabName={contextTab?.name ?? ''}
				onPinTab={savePinnedTab}
			/>
		</div>
	</div>
	{/if}

	<!-- Memory panel (slide-out overlay) -->
	{#if showMemoryPanel}
		<ChatMemoryPanel
			presetId={contextTab?.selectedPreset ?? null}
			formData={contextTab?.formData ?? {}}
			onClose={() => (showMemoryPanel = false)}
		/>
	{/if}

	<!-- My Tools panel (slide-out overlay, persistent per-user tool opt-out) -->
	{#if showToolPreferencesPanel}
		<ChatToolPreferencesPanel
			llmConfigId={selectedConfigId || null}
			onClose={() => (showToolPreferencesPanel = false)}
			onChanged={loadMyToolPreferences}
		/>
	{/if}

</div>
