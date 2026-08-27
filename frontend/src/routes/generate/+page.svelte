<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { tabsStore, activeTab, generatingTab, isActiveTabGenerating } from '$lib/stores/tabs';
	import type { PromptTabData } from '$lib/types/tabs';
	import { authStore } from '$lib/stores/auth';
	import { api, type GenerationRequest, type PromptPair } from '$lib/services/api';
	import { buildSegmentsPayload, buildVariablesPayload, mapGenerationFiles } from '$lib/utils/generationOrchestrator';
	import { findUndefinedVariableUsages } from '$lib/utils/promptVariables';
	import { buildSessionRestoreTabPatch } from '$lib/utils/sessionRestore';
	import { collectTabSessionData, normalizeSessionBaselineFormData } from '$lib/utils/sessionTabState';
	import { WebSocketService, createGenerationSocket } from '$lib/services/websocket';
	import type { WebSocketMessage } from '$lib/services/websocket';
	import { dispatchGenerationMessage } from '$lib/stores/generation';
	import GenerationPanel from '$lib/components/GenerationPanel.svelte';
	import type DynamicForm from '$lib/components/DynamicForm.svelte';
	import SessionPill from '$lib/components/session/SessionPill.svelte';
	import TabBar from './components/TabBar.svelte';
	import PromptSection from './components/PromptSection.svelte';
	import GenerationPanels from './components/GenerationPanels.svelte';
	import PresetControls from './components/PresetControls.svelte';
	import GenerationFormPane from './components/GenerationFormPane.svelte';
	import GenerationWorkbenchPane from './components/GenerationWorkbenchPane.svelte';
	import { resolveNegativeApplicability } from '$lib/generation/negativeApplied';
	import { leadIndex } from '$lib/generation/leadFile';
	import { normalizeFileType } from '$lib/utils/fileType';
	import { galleryTotal } from '$lib/components/workbench/workbenchGallery';
	import GenerationSettingsPanel from './components/GenerationSettingsPanel.svelte';
	import LastGenerationsDrawer from '$lib/components/generation-panel/LastGenerationsDrawer.svelte';
	import { resolvePromptSegments } from '$lib/utils/promptSegments';
	import type { SegmentJoin } from '$lib/utils/richSegments';
	import { unlockGenerationSoundContext } from '$lib/utils/generationSounds';
	import { keybindingsStore } from '$lib/stores/keybindings';
	import { isMobile, viewportWidth } from '$lib/stores/viewport';
	import { settingsPaneWidth } from '$lib/stores/generationLayout';
	import UnifiedAIChat from '$lib/components/UnifiedAIChat.svelte';
	import { resolveDirectorCapabilities, normalizeDirectorValue, validateDirector, buildDirectorSubmission, representativeDirectorPrompt, dereferenceFormMediaRefs, seedDirectorPromptFromLegacyText } from '$lib/utils/videoDirector';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import { resolveMusicDirectorCapabilities, normalizeMusicDirectorValue, validateMusicDirector, buildMusicDirectorSubmission } from '$lib/utils/musicDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
	import { resolveVariant } from '$lib/utils/variants';
	import { isPromptlessMode } from '$lib/utils/promptlessMode';
	import { toasts } from '$lib/stores/toast';
	import { formValidationStore } from '$lib/stores/formValidation';
	import { classifyGenerationStartError } from '$lib/utils/formValidationErrors';
	import { resolveDefaultModeSelection } from '$lib/utils/modeAutoSelect';
	import { buildModeSwitchPatch, seedModeStateFromSessionData } from '$lib/utils/modeState';
	import { describePresetsEmptyState } from '$lib/utils/presetsEmptyState';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import { EmptyState, Button } from '$lib/components/ui';

	let ws: WebSocketService | null = null;
	let isConnected = false;
	let presets: any[] = [];

	// Bumped whenever the active tab's generation completes, so the "last
	// generations" drawer refetches while it's open instead of going stale.
	let lastGenerationsRefreshSignal = 0;

	// Readiness: fetched once when the preset list turns up empty, to explain
	// *why* (no backend configured, no presets assigned, …) instead of leaving
	// a fresh user on a bare "nothing selectable" screen. Never polled.
	let readiness: ReadinessReport | null = null;
	let readinessLoading = false;
	let readinessFetched = false;
	$: isAdmin = $authStore.user?.account_type === 'ADMIN';
	$: presetsEmptyState = describePresetsEmptyState(readiness, isAdmin);

	async function loadReadinessIfPresetsEmpty() {
		if (readinessFetched || presets.length > 0) return;
		readinessFetched = true;
		readinessLoading = true;
		try {
			readiness = await api.getReadiness();
		} catch (error) {
			console.error('Failed to load readiness:', error);
			readiness = null;
		} finally {
			readinessLoading = false;
		}
	}

	// Mobile swipe panel state (0 = preset/session, 1 = form, 2 = generation, 3 = LLM chat)
	const MOBILE_PANEL_COUNT = 4;
	let mobilePanel = 2;
	let touchStartX = 0;
	let touchStartY = 0;
	let touchDeltaX = 0;
	let isSwiping = false;
	const SWIPE_THRESHOLD = 50;

	let swipeEligible = true;

	// A horizontal drag inside these targets is the target's own gesture
	// (slider drag, text selection, horizontal scroll) — never a panel swipe.
	function swipeExempt(target: EventTarget | null): boolean {
		if (!(target instanceof Element)) return false;
		return !!target.closest(
			'input, textarea, select, [contenteditable="true"], [data-no-swipe], .overflow-x-auto'
		);
	}

	function handleTouchStart(e: TouchEvent) {
		touchStartX = e.touches[0].clientX;
		touchStartY = e.touches[0].clientY;
		touchDeltaX = 0;
		isSwiping = false;
		swipeEligible = !swipeExempt(e.target);
	}

	function handleTouchMove(e: TouchEvent) {
		const dx = e.touches[0].clientX - touchStartX;
		const dy = e.touches[0].clientY - touchStartY;

		if (!isSwiping && swipeEligible && Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy)) {
			isSwiping = true;
		}

		if (isSwiping) {
			e.preventDefault();
			touchDeltaX = dx;
		}
	}

	function handleTouchEnd() {
		if (isSwiping) {
			if (touchDeltaX > SWIPE_THRESHOLD && mobilePanel > 0) {
				mobilePanel -= 1;
			} else if (touchDeltaX < -SWIPE_THRESHOLD && mobilePanel < MOBILE_PANEL_COUNT - 1) {
				mobilePanel += 1;
			}
		}
		touchDeltaX = 0;
		isSwiping = false;
	}

	// The browser fires touchcancel — not touchend — when it takes the gesture
	// over mid-swipe (long-press text selection, notification shade, screenshot
	// gesture). Without this reset the half-swiped transform stays latched and
	// the carousel appears permanently misaligned.
	function handleTouchCancel() {
		touchDeltaX = 0;
		isSwiping = false;
	}

	// Programmatic panel changes (indicator taps, auto-switch on submit) happen
	// OUTSIDE the swipe container, so they must also discard any in-flight or
	// stranded swipe delta — otherwise they jump panels but keep the offset.
	function setMobilePanel(i: number) {
		mobilePanel = i;
		touchDeltaX = 0;
		isSwiping = false;
	}

	// Off-screen panels (e.g. the LLM chat composer autofocusing its input on
	// mount) can trigger the browser's default focus scroll-into-view even
	// though this container never scrolls under user control. That latches a
	// permanent scrollLeft the translateX carousel math never accounts for, so
	// stamp it back to 0 the instant it happens.
	let mobilePanelsContainer: HTMLDivElement;
	function resetSwipeContainerScroll() {
		if (mobilePanelsContainer.scrollLeft !== 0) mobilePanelsContainer.scrollLeft = 0;
		if (mobilePanelsContainer.scrollTop !== 0) mobilePanelsContainer.scrollTop = 0;
	}
	let mounted = false;
	let isLoading = false;
	let canGenerate = false;
	// First reason `canGenerate` is false — surfaced on the generation bar's
	// mark/subline (GenerationPanel) instead of leaving a disabled Generate
	// button with no explanation.
	let generateDisabledReason: string | undefined;
	let isReloadingPreset = false;
	let hasRestoredGenerations = false;

	// Settings pane width: fixed per viewport tier, not user-resizable.
	$: leftPanelWidth = settingsPaneWidth($viewportWidth);

	// Object to store DynamicForm component references per tab
	let dynamicFormRefs: Record<string, DynamicForm> = {};

	// Preset vars cache for multi-prompt support
	let presetVars: Record<string, Record<string, any>> = {};
	const presetVarsInFlight = new Map<string, Promise<void>>();
	// Toast once per preset id on load failure, not on every retry — a preset
	// that keeps failing would otherwise re-toast on every reactive tick (see
	// loadPresetVars's guard comment below).
	const presetVarsErrorShown = new Set<string>();

	// Reactive subscriptions
	$: tabs = $tabsStore.tabs;
	$: activeTabId = $tabsStore.activeTabId;
	// The generation bar's session cluster needs the same mode/variant shape
	// PresetHeader gets per-tab below.
	$: activeTabModes = (modesPerTab[activeTabId] || []).map((m) => ({
		id: m.name,
		label: m.label,
		variants: m.variants,
		sourcePlugin: m.source_plugin
	}));
	$: currentTab = $activeTab;
	// The generation bar's session cluster needs the same version-drift check
	// PresetHeader/SessionPill get per-tab below.
	$: currentTabPresetVersion = presets.find((p: any) => p.id === currentTab.selectedPreset)?.version;
	$: generatingTabName = $generatingTab?.name;
	$: generation = currentTab.generation;
	$: isGenerating = generation.isGenerating;

	// Get num_prompts for current preset (default 1)
	$: numPrompts = presetVars[currentTab.selectedPreset || '']?.num_prompts || 1;
	$: currentPresetVars = presetVars[currentTab.selectedPreset || ''] || {};
	$: negativePromptSupported =
		currentPresetVars.supports_negative_prompt !== false &&
		currentPresetVars.negative_prompt_supported !== false;

	// The negative editor goes visibly inert when the resolved guidance
	// can't reach the model (guidance <= 1 with NAG off). Derived from the same
	// reaction-resolved form values the backend binds, so a turbo/no-CFG profile
	// shows the notice without a round trip. Only meaningful for the standard
	// prompt editor — relay/director/promptless modes don't have a plain negative.
	$: negativeInert =
		negativePromptSupported &&
		!promptRelayActive &&
		!videoDirectorActive &&
		!musicDirectorActive &&
		!promptlessActive &&
		resolveNegativeApplicability(currentTab.formData, currentPresetVars.negative_applied_fields) ===
			'inert';

	// Prompt Relay: modes (per current preset) whose prompt section uses the
	// timeline-based Prompt Relay editor instead of the standard prompt editors.
	$: promptRelayModes = presetVars[currentTab.selectedPreset || '']?.prompt_relay_modes || [];
	$: promptRelayActive =
		!!currentTab.selectedMode && promptRelayModes.includes(currentTab.selectedMode);

	// Video Director: modes (per current preset) whose prompt section uses the
	// structured multi-mode video composition editor instead of the standard
	// prompt editors (or Prompt Relay — director takes precedence when both
	// happen to be configured for the same mode).
	//
	// `videoDirectorCaps` is memoized on (preset id, raw var JSON) rather than
	// recomputed to a fresh object on every reactive tick: this page's `$:`
	// blocks re-run on every tabsStore update (e.g. every keystroke inside the
	// director editor, since `currentTab` gets a new object identity on each
	// store write), and a new `capabilities` object reference flowing into
	// VideoDirectorEditor on every keystroke is unnecessary prop churn that
	// widens the surface for effect loops in the component tree below.
	// Effective capabilities for the ACTIVE preset mode: base `modes` merged
	// with `preset_mode_overrides[currentTab.selectedMode]` (resolveDirectorCapabilities
	// -- the single entry point every capability read in this file goes
	// through). The cache key includes `selectedMode` alongside the preset id
	// and raw JSON: switching preset modes on the same preset (e.g. H3's
	// "video" <-> "refs") changes the resolved result even though neither of
	// the other two key parts changed.
	let videoDirectorCapsCache: { key: string; caps: DirectorCapabilities | null } | null = null;
	$: videoDirectorCapsRaw = presetVars[currentTab.selectedPreset || '']?.video_director;
	$: videoDirectorCapsKey = `${currentTab.selectedPreset || ''}:${currentTab.selectedMode || ''}:${JSON.stringify(videoDirectorCapsRaw ?? null)}`;
	$: videoDirectorCaps = (() => {
		if (videoDirectorCapsCache && videoDirectorCapsCache.key === videoDirectorCapsKey) {
			return videoDirectorCapsCache.caps;
		}
		const caps = resolveDirectorCapabilities(videoDirectorCapsRaw, currentTab.selectedMode);
		videoDirectorCapsCache = { key: videoDirectorCapsKey, caps };
		return caps;
	})();
	$: videoDirectorActive =
		!!videoDirectorCaps &&
		!!currentTab.selectedMode &&
		(videoDirectorCaps.presetModes === null || videoDirectorCaps.presetModes.includes(currentTab.selectedMode));

	// Music Director: same shape/memoization reasoning as videoDirectorCaps
	// above -- a preset opts in via `vars.music_director` (docs/music-director.md).
	let musicDirectorCapsCache: { key: string; caps: MusicDirectorCapabilities | null } | null = null;
	$: musicDirectorCapsRaw = presetVars[currentTab.selectedPreset || '']?.music_director;
	$: musicDirectorCapsKey = `${currentTab.selectedPreset || ''}:${currentTab.selectedMode || ''}:${JSON.stringify(musicDirectorCapsRaw ?? null)}`;
	$: musicDirectorCaps = (() => {
		if (musicDirectorCapsCache && musicDirectorCapsCache.key === musicDirectorCapsKey) {
			return musicDirectorCapsCache.caps;
		}
		const caps = resolveMusicDirectorCapabilities(musicDirectorCapsRaw, currentTab.selectedMode);
		musicDirectorCapsCache = { key: musicDirectorCapsKey, caps };
		return caps;
	})();
	$: musicDirectorActive =
		!!musicDirectorCaps &&
		!!currentTab.selectedMode &&
		(musicDirectorCaps.presetModes === null || musicDirectorCaps.presetModes.includes(currentTab.selectedMode));

	// `Tab.videoDirector` is a field of its own — modeState.ts never routes it
	// through the per-mode prompt cache — so a mode that only just gained
	// Director (H3's `refs` did, alongside `video`) leaves whatever text a tab
	// already carries in its plain prompt fields with nowhere to land in the
	// fresh document the editor mounts with: the editor renders empty, the
	// gate below reports no prompt, and Generate goes dead with the real
	// prompt sitting invisible behind the swapped-out editor. One-time,
	// self-gating (see seedDirectorPromptFromLegacyText) — only ever migrates
	// text into a still-untouched document, never overwrites or resurrects.
	$: if (videoDirectorActive && videoDirectorCaps) {
		const legacyPromptText =
			currentTab.promptSegments && currentTab.promptSegments.length > 0
				? resolvePromptSegments(currentTab.promptSegments)
				: currentTab.prompt || '';
		const seeded = seedDirectorPromptFromLegacyText(currentTab.videoDirector, videoDirectorCaps, legacyPromptText);
		if (seeded) {
			tabsStore.updateTab(activeTabId, { videoDirector: seeded });
		}
	}

	// Promptless: modes (per current preset) that need no prompt at all (upscale,
	// slow-motion, LTX utility passes). The prompt pane is hidden and Generate no
	// longer requires prompt text. See docs/presets.md `promptless_modes`.
	$: promptlessActive = isPromptlessMode(currentPresetVars, currentTab.selectedMode);

	// Load preset vars when preset changes
	$: if (currentTab.selectedPreset && mounted) {
		loadPresetVars(currentTab.selectedPreset);
	}

	async function loadPresetVars(presetId: string) {
		if (!presetId || presetVars[presetId]) {
			return;
		}
		const existing = presetVarsInFlight.get(presetId);
		if (existing) return existing;

		const request = (async () => {
			try {
				const response = await api.getPreset(presetId);
				if (response.success && response.data) {
					presetVars[presetId] = response.data.vars || {};
					presetVars = { ...presetVars };
				}
			} catch (error) {
				console.error('Failed to load preset vars:', error);
				if (!presetVarsErrorShown.has(presetId)) {
					presetVarsErrorShown.add(presetId);
					toasts.error("Couldn't load this preset's settings. Some options may be missing — try reselecting it.");
				}
			} finally {
				presetVarsInFlight.delete(presetId);
			}
		})();
		presetVarsInFlight.set(presetId, request);
		return request;
	}

	onMount(async () => {
		mounted = true;

		// One-time migration: Clear selectedMode for all tabs if no preset selected
		tabs.forEach(tab => {
			if (!tab.selectedPreset && tab.selectedMode) {
				tabsStore.updateTab(tab.id, { selectedMode: null });
			}
		});

		// Initialize WebSocket
		ws = createGenerationSocket();
		ws.onConnectionChange((connected) => {
			isConnected = connected;
			if (connected && !hasRestoredGenerations) {
				hasRestoredGenerations = true;
				restoreActiveGenerations();
				restoreQueuedGenerations();
			}
		});
		ws.connect();

		// Register generate-context keybinding handlers
		keybindingsStore.registerHandler('start_generation', () => {
			if (canGenerate && !isGenerating) {
				startGeneration();
			}
		});
		keybindingsStore.registerHandler('new_tab', () => {
			addTab();
		});
		keybindingsStore.registerHandler('close_tab', () => {
			if (tabs.length > 1) {
				removeTab(activeTabId);
			}
		});
		keybindingsStore.registerHandler('toggle_left_panel', () => {
			tabsStore.updateTab(activeTabId, { leftPanelCollapsed: !currentTab.leftPanelCollapsed });
		});

		// Load presets
		isLoading = true;
		try {
			const response = await api.listPresets();
			if (response.success && response.data) {
				presets = response.data;
			}
		} catch (error) {
			console.error('Failed to load presets:', error);
		} finally {
			isLoading = false;
		}
		await loadReadinessIfPresetsEmpty();

		// Restore sessions for tabs that have selectedSessionId
		await restoreTabSessions();
	});

	async function restoreTabSessions() {
		const currentTabs = $tabsStore.tabs;

		await Promise.all(currentTabs.map(async (tab) => {
			// Skip if no session to restore
			if (!tab.selectedSessionId || !tab.selectedMode) return;

			try {
				const response = await api.getSessionById(tab.selectedSessionId);
				if (response.success && response.data) {
					const session = response.data;
					const modeData = session.data[tab.selectedMode];

					if (modeData) {
						// Non-blocking notice: the mode/variant selectors re-validate against
						// live preset data once modes/variants load (see the modesPerTab
						// reactive block below), so an unknown saved variant just falls back
						// to the default there rather than failing here.
						const presetForSession = presets.find((p) => p.id === (modeData.selectedPreset || tab.selectedPreset));
						if (modeData.presetVersion && presetForSession?.version && modeData.presetVersion !== presetForSession.version) {
							toasts.warning(
								`Session "${tab.name}" was saved with preset version ${modeData.presetVersion}, now at ${presetForSession.version} — some fields may have changed.`
							);
						}

						// Restore session data to tab (keep selectedSessionId!). Shared with
						// SessionPill's manual picker and sessions.ts's loadSession —
						// see sessionRestore.ts's header for why (this path was the one
						// missing `variables`, `promptTabs`, and `leftPanelCollapsed`).
						const restoredPatch = buildSessionRestoreTabPatch(modeData, {
							selectedBackendId: tab.selectedBackendId,
							promptPanelWidth: tab.promptPanelWidth
						});
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
							selectedSessionId: tab.selectedSessionId, // Preserve session ID
							...restoredPatch,
							// Seed the per-mode cache from every OTHER mode this session has
							// data for, so a live mode switch after this restore picks up
							// that mode's saved config instead of starting empty.
							modeStateByMode: restoredTab.modeStateByMode,
							// The bar is allowed to remount, but not to redefine this server
							// snapshot as whatever draft happens to be in the tab then.
							savedSessionSignature: JSON.stringify(
								collectTabSessionData(restoredTab, tab.selectedMode, session.data, presetForSession?.version)
							),
							sessionBaselineAwaitingFormNormalization: true
						});
					}
				}
			} catch (error) {
				console.error(`[TabRestore] Failed to restore session for tab ${tab.name}:`, error);
				// Clear the invalid session ID
				tabsStore.updateTab(tab.id, {
					selectedSessionId: null,
					savedSessionSignature: null,
					sessionBaselineAwaitingFormNormalization: false
				});
			}
		}));
	}

	function generationTimestampMs(value?: string | number | null): number | null {
		if (value === undefined || value === null) return null;
		const numeric = Number(value);
		if (Number.isFinite(numeric)) return numeric < 1_000_000_000_000 ? numeric * 1000 : numeric;
		const parsed = Date.parse(String(value));
		return Number.isNaN(parsed) ? null : parsed;
	}

	async function restoreActiveGenerations() {
		const currentTabs = $tabsStore.tabs;

		await Promise.all(currentTabs.map(async (tab) => {
			const generationId = tab.activeGenerationId;
			if (!generationId) return;

			try {
				const statusResponse = await api.getGenerationStatus(generationId);

				if (statusResponse.success && statusResponse.data) {
					const status = statusResponse.data;
					const generationStatus = status.status;

					if (generationStatus === 'pending' || generationStatus === 'running') {
						// Still running: restore generating state and re-subscribe
						tabsStore.updateTab(tab.id, {
							generation: {
								...tab.generation,
								isGenerating: true,
								startedAt:
									generationTimestampMs(status.started_at ?? status.created_at) ??
									tab.generation.startedAt ??
									Date.now(),
								currentGeneration: {
									...status,
									id: generationId,
									generation_id: generationId
								}
							}
						});

						if (ws) {
							ws.subscribe(generationId, (message: WebSocketMessage) => {
								handleGenerationMessage(message);
							});
						}
					} else if (generationStatus === 'completed') {
						// Completed during refresh: fetch final results
						try {
							const historyResponse = await api.getGenerationById(generationId, false, true);
							if (historyResponse.success && historyResponse.data) {
									const { images, videos, audios, meshes, totalItems } = mapGenerationFiles(
										historyResponse.data.files || [],
										generationId
									);

									// Lead with the newest derived file (e.g. an enhance pass) when
									// present. Order must match Workbench's gallery index chain:
									// images, videos, audios, meshes.
									const workbenchIndex = leadIndex([...images, ...videos, ...audios, ...meshes]);
									const leadImage = workbenchIndex < images.length ? images[workbenchIndex] : null;
									const leadVideo =
										!leadImage && workbenchIndex < images.length + videos.length
											? videos[workbenchIndex - images.length]
											: null;
									const leadAudio =
										!leadImage && !leadVideo && workbenchIndex < images.length + videos.length + audios.length
											? audios[workbenchIndex - images.length - videos.length]
											: null;
									const leadMesh =
										!leadImage && !leadVideo && !leadAudio
											? meshes[workbenchIndex - images.length - videos.length - audios.length] || null
											: null;

								tabsStore.updateTab(tab.id, {
									activeGenerationId: null,
									generation: {
										...tab.generation,
										isGenerating: false,
										currentGeneration: leadImage ? {
											status: 'completed',
											id: generationId,
											generation_id: generationId,
											current_image: leadImage.url,
											file_type: 'image'
										} : leadVideo ? {
											status: 'completed',
											id: generationId,
											generation_id: generationId,
											current_video: leadVideo.url,
											file_type: 'video'
										} : leadAudio ? {
											status: 'completed',
											id: generationId,
											generation_id: generationId,
											current_audio: leadAudio,
											file_type: 'audio'
										} : leadMesh ? {
											status: 'completed',
											id: generationId,
											generation_id: generationId,
											current_mesh: leadMesh.url,
											file_type: 'mesh'
										} : {
											status: 'completed',
											id: generationId,
											generation_id: generationId
										},
										batchImages: images,
										batchVideos: videos,
										batchAudios: audios,
										batchMeshes: meshes,
										workbenchIndex,
										workbenchTotal: totalItems
									}
								});
							} else {
								// History not found, clear state
								tabsStore.updateTab(tab.id, { activeGenerationId: null });
							}
						} catch {
							tabsStore.updateTab(tab.id, { activeGenerationId: null });
						}
					} else {
						// Failed or cancelled
						tabsStore.updateTab(tab.id, {
							activeGenerationId: null,
							generation: {
								...tab.generation,
								isGenerating: false,
								currentGeneration: generationStatus === 'failed' ? {
									status: 'failed',
									id: generationId,
									generation_id: generationId,
									message: status.message || 'Generation failed'
								} : null
							}
						});
					}
				} else {
					// Status endpoint returned error (generation not found)
					tabsStore.updateTab(tab.id, { activeGenerationId: null });
				}
			} catch {
				// Server may have restarted, generation not found
				console.warn(`[RestoreGeneration] Could not restore generation ${generationId} for tab ${tab.name}`);
				tabsStore.updateTab(tab.id, { activeGenerationId: null });
			}
		}));
	}

	// Restores each tab's queued (pending) and running work from the backend
	// queue after a reload. Complements restoreActiveGenerations(), which only
	// knows about the single legacy activeGenerationId per tab — a tab can now
	// have several outstanding generations enqueued at once.
	async function restoreQueuedGenerations() {
		const currentTabs = $tabsStore.tabs;
		let snapshot;
		try {
			const response = await api.getGenerationQueue();
			if (!response.success || !response.data) return;
			snapshot = response.data;
		} catch (error) {
			console.warn('[RestoreQueue] Could not restore queue:', error);
			return;
		}

		await Promise.all(currentTabs.map(async (tab) => {
			try {
				const pending = snapshot.pending.filter((item) => item.tab_id === tab.id);
				const running = snapshot.running.filter((item) => item.tab_id === tab.id);
				if (pending.length === 0 && running.length === 0) return;

				const latestTab = $tabsStore.tabs.find((t) => t.id === tab.id) || tab;
				const existingQueue = latestTab.generation.queue || [];
				const existingIds = new Set(existingQueue.map((q) => q.generation_id));

				const newEntries = [
					...pending
						.filter((p) => !existingIds.has(p.generation_id))
						.map((p) => ({
							generation_id: p.generation_id,
							queue_position: p.queue_position,
							status: 'pending' as const
						})),
					...running
						.filter((r) => !existingIds.has(r.generation_id))
						.map((r) => ({
							generation_id: r.generation_id,
							queue_position: null,
							status: 'running' as const
						}))
				];

				if (newEntries.length === 0) return;

				// Adopt the first running item as the tab's live display if the
				// tab doesn't already have one restored via activeGenerationId.
				const firstRunning = running.find((r) => !existingIds.has(r.generation_id));
				const shouldAdoptAsCurrent = firstRunning && !latestTab.generation.currentGeneration;

				tabsStore.updateTab(tab.id, {
					...(shouldAdoptAsCurrent ? { activeGenerationId: firstRunning!.generation_id } : {}),
					generation: {
						...latestTab.generation,
						queue: [...existingQueue, ...newEntries],
						...(shouldAdoptAsCurrent
							? {
									isGenerating: true,
									startedAt: latestTab.generation.startedAt ?? Date.now(),
									currentGeneration: {
										id: firstRunning!.generation_id,
										generation_id: firstRunning!.generation_id,
										status: 'running',
										progress: firstRunning!.progress
									}
								}
							: {})
					}
				});

				if (ws) {
					for (const entry of newEntries) {
						ws.subscribe(entry.generation_id, (message: WebSocketMessage) => {
							handleGenerationMessage(message);
						});
					}
				}
			} catch (error) {
				console.warn(`[RestoreQueue] Could not restore queue for tab ${tab.name}:`, error);
			}
		}));
	}

	onDestroy(() => {
		if (ws) {
			ws.disconnect();
		}
		// Unregister generate-context keybinding handlers
		keybindingsStore.unregisterHandler('start_generation');
		keybindingsStore.unregisterHandler('new_tab');
		keybindingsStore.unregisterHandler('close_tab');
		keybindingsStore.unregisterHandler('toggle_left_panel');
	});

	// Reactive store for modes per tab
	let modesPerTab: Record<string, any[]> = {};
	let modesPresetPerTab: Record<string, string> = {};
	let modesDefaultPerTab: Record<string, string> = {};
	const modesInFlight = new Map<string, Promise<void>>();
	// Toast once per (tab, preset) on load failure — see presetVarsErrorShown.
	const modesErrorShown = new Set<string>();
	// Retry once per (tab, preset) on load failure — see loadModesForTab. A
	// single dropped request (common on a flaky mobile connection right after
	// picking a preset) must not strand the tab on the "select a mode"
	// placeholder forever with no mode ever auto-selected.
	const modesRetriedFor = new Set<string>();

	// Only the visible tab needs its mode manifest. Inactive tabs retain their
	// persisted selection and fetch metadata when the user switches to them.
	$: {
		const tab = tabs.find((candidate) => candidate.id === activeTabId);
		if (tab?.selectedPreset && modesPresetPerTab[tab.id] !== tab.selectedPreset) {
			loadModesForTab(tab.id, tab.selectedPreset);
		}

		if (tab?.selectedPreset && tab.selectedMode && modesPresetPerTab[tab.id] === tab.selectedPreset) {
			const availableModes = modesPerTab[tab.id] || [];
			const modeInfo = availableModes.find(m => m.name === tab.selectedMode);

			if (!modeInfo) {
				// The persisted/requested mode no longer exists on this preset (a
				// stale session, or the preset dropped it) — land on a usable mode
				// instead of leaving the tab with none selected, same as a fresh
				// preset pick.
				const fallback = resolveDefaultModeSelection(availableModes, modesDefaultPerTab[tab.id] ?? null);
				tabsStore.updateTab(tab.id, {
					...(fallback?.mode ? buildModeSwitchPatch(tab, tab.selectedMode, fallback.mode) : {}),
					selectedMode: fallback?.mode ?? null,
					selectedVariant: fallback?.variant ?? null
				});
			} else {
				// Keep the selected variant valid for the current mode, falling back
				// to the mode's default variant (non-fatal) when it no longer exists.
				const resolved = resolveVariant(modeInfo.variants, tab.selectedVariant ?? null);
				if (resolved !== (tab.selectedVariant ?? null)) {
					tabsStore.updateTab(tab.id, { selectedVariant: resolved });
				}
			}
		}
	}

	async function loadModesForTab(tabId: string, presetId: string) {
		const requestKey = `${tabId}:${presetId}`;
		const existing = modesInFlight.get(requestKey);
		if (existing) return existing;

		const request = (async () => {
			try {
				const response = await api.getPresetModes(presetId);
				const currentTab = $tabsStore.tabs.find((tab) => tab.id === tabId);
				if (response.success && response.data && currentTab?.selectedPreset === presetId) {
					const modes = response.data.modes;
					modesPerTab[tabId] = modes;
					modesPresetPerTab[tabId] = presetId;
					modesDefaultPerTab[tabId] = response.data.default_mode || '';
					modesPerTab = { ...modesPerTab };
					modesPresetPerTab = { ...modesPresetPerTab };
					modesDefaultPerTab = { ...modesDefaultPerTab };

					// Auto-select a mode when the tab has none yet (fresh preset pick,
					// first mount with no persisted mode) — never overrides a mode a
					// session/deep-link already set. Mirrors the admin-preview pattern
					// in previewGeneration.ts (`defaultMode || modes[0].name`).
					if (!currentTab.selectedMode) {
						const selection = resolveDefaultModeSelection(modes, response.data.default_mode);
						if (selection) {
							tabsStore.updateTab(tabId, {
								selectedMode: selection.mode,
								selectedVariant: selection.variant
							});
						}
					}
				}
			} catch (error) {
				console.error('Failed to load modes:', error);
				if (!modesErrorShown.has(requestKey)) {
					modesErrorShown.add(requestKey);
					toasts.error("Couldn't load this preset's modes. Try reselecting the preset.");
				}
				// modesPresetPerTab[tabId] is only set on success, so nothing else
				// re-triggers this fetch on its own — a reactive block only reruns
				// when a tracked store value changes, and a failed request changes
				// none of them. Retry once, after a beat, so a single dropped
				// request self-heals instead of leaving the tab permanently on the
				// "select a mode" placeholder.
				if (!modesRetriedFor.has(requestKey)) {
					modesRetriedFor.add(requestKey);
					setTimeout(() => {
						const stillOnThisPreset = $tabsStore.tabs.find((t) => t.id === tabId)?.selectedPreset === presetId;
						if (stillOnThisPreset) loadModesForTab(tabId, presetId);
					}, 1500);
				}
			} finally {
				modesInFlight.delete(requestKey);
			}
		})();
		modesInFlight.set(requestKey, request);
		return request;
	}

	// The preset-declared separator between enabled prompt segments
	// (`vars.prompt.segment_join` in preset.yml — `paragraph` for song-section-style
	// presets, `comma` for every existing image/video preset).
	function segmentJoinForPreset(presetId: string | null | undefined): SegmentJoin {
		return presetVars[presetId || '']?.prompt?.segment_join === 'paragraph' ? 'paragraph' : 'comma';
	}

	function createTabHandlers(tabId: string) {
		return {
			handlePresetChange: (presetId: string) => {
				tabsStore.updateTab(tabId, {
					selectedPreset: presetId,
					selectedMode: null,  // Clear mode when preset changes
					selectedVariant: null,
					formData: {},
					selectedSessionId: null,  // Clear session when preset changes
					savedSessionSignature: null,
					sessionBaselineAwaitingFormNormalization: false,
					sourcePromptId: null,
					positiveSegmentsCollapsed: undefined,
					negativeSegmentsCollapsed: undefined,
					// Mode names are only meaningful within their own preset — drop
					// the per-mode cache so a new preset reusing a mode name
					// (e.g. "video") can't inherit another preset's segments/form data.
					modeStateByMode: {}
				});
				delete modesPerTab[tabId];
				delete modesPresetPerTab[tabId];
				modesPerTab = { ...modesPerTab };
				modesPresetPerTab = { ...modesPresetPerTab };
				// Load modes for new preset
				if (presetId) {
					loadModesForTab(tabId, presetId);
				}
			},
			handleModeChange: (mode: string) => {
				const tab = $tabsStore.tabs.find((candidate) => candidate.id === tabId);
				if (!tab) return;
				const modeInfo = (modesPerTab[tabId] || []).find((m) => m.name === mode);
				tabsStore.updateTab(tabId, {
					...buildModeSwitchPatch(tab, tab.selectedMode, mode),
					selectedMode: mode,
					selectedVariant: resolveVariant(modeInfo?.variants, null)
				});
			},
			handleVariantChange: (variantName: string) => {
				tabsStore.updateTab(tabId, { selectedVariant: variantName });
			},
			handlePromptChange: (prompt: string) => {
				const tab = $tabsStore.tabs.find((candidate) => candidate.id === tabId);
				if (!tab || tab.prompt === prompt) return;
				tabsStore.updateTab(tabId, {
					prompt
				});
			},
			handlePromptSegmentsChange: (segments: any[]) => {
				const tab = $tabsStore.tabs.find((candidate) => candidate.id === tabId);
				const mergedPrompt = resolvePromptSegments(segments, segmentJoinForPreset(tab?.selectedPreset));
				tabsStore.updateTab(tabId, {
					promptSegments: segments,
					prompt: mergedPrompt  // SYNC the string field
				});
			},
			handleNegativePromptChange: (prompt: string) => {
				const tab = $tabsStore.tabs.find((candidate) => candidate.id === tabId);
				if (!tab || tab.negativePrompt === prompt) return;
				tabsStore.updateTab(tabId, {
					negativePrompt: prompt
				});
			},
			handleNegativePromptSegmentsChange: (segments: any[]) => {
				const tab = $tabsStore.tabs.find((candidate) => candidate.id === tabId);
				const mergedPrompt = resolvePromptSegments(segments, segmentJoinForPreset(tab?.selectedPreset));
				tabsStore.updateTab(tabId, {
					negativePromptSegments: segments,
					negativePrompt: mergedPrompt  // SYNC the string field
				});
			},
			// Multi-prompt handlers
			handlePromptTabsChange: (promptTabs: PromptTabData[]) => {
				tabsStore.updateTab(tabId, { promptTabs });
			},
			handleActivePromptTabChange: (activePromptTab: number) => {
				tabsStore.updateTab(tabId, { activePromptTab });
			}
		};
	}

	function handleFormDataChange(tabId: string, formData: Record<string, unknown>) {
		const tab = $tabsStore.tabs.find((candidate) => candidate.id === tabId);
		if (!tab) return;

		if (tab.sessionBaselineAwaitingFormNormalization && tab.selectedSessionId && tab.selectedMode) {
			tabsStore.updateTab(tabId, {
				formData,
				savedSessionSignature: normalizeSessionBaselineFormData(
					tab.savedSessionSignature,
					tab.selectedMode,
					formData
				),
				sessionBaselineAwaitingFormNormalization: false
			});
			return;
		}

		tabsStore.updateTab(tabId, { formData });
	}

	// Handle preset reload from PresetHeader
	async function handlePresetReload(tabId: string, presetId: string | null) {
		if (!presetId || isReloadingPreset) return;

		try {
			isReloadingPreset = true;

			// Call backend to reload preset from disk
			const response = await api.reloadPreset(presetId);

			if (response.success) {
				// Force the DynamicForm to reload its schema
				const formRef = dynamicFormRefs[tabId];
				if (formRef) {
					formRef.forceReload();
				}
			}
		} catch (error) {
			console.error('Failed to reload preset:', error);
		} finally {
			isReloadingPreset = false;
		}
	}

	async function startGeneration() {
		unlockGenerationSoundContext();

		// Shuffle chips with shuffle mode enabled BEFORE collecting data
		let shuffledPromptSegments = [...(currentTab.promptSegments || [])];
		let shuffledNegativePromptSegments = [...(currentTab.negativePromptSegments || [])];
		let hasShuffledChips = false;

		// Helper function to shuffle a chip's value
		function shuffleChip(chip: any): any {
			if (!chip.shuffle || !chip.allValues || chip.allValues.length <= 1) {
				return chip;
			}

			// Pick a random value different from current
			const availableValues = chip.allValues.filter((v: any) => v.id !== chip.valueId);
			if (availableValues.length === 0) {
				return chip;
			}

			const randomValue = availableValues[Math.floor(Math.random() * availableValues.length)];
			return {
				...chip,
				valueId: randomValue.id,
				label: randomValue.label,
				value: randomValue.value
			};
		}

		// Process positive prompt segments
		for (let i = 0; i < shuffledPromptSegments.length; i++) {
			const segment = shuffledPromptSegments[i];
			if (segment.chips && Object.keys(segment.chips).length > 0) {
				const updatedChips: Record<string, any> = {};
				let segmentUpdated = false;

				for (const [chipId, chipData] of Object.entries(segment.chips)) {
					const shuffled = shuffleChip(chipData);
					updatedChips[chipId] = shuffled;
					if (shuffled !== chipData) {
						segmentUpdated = true;
						hasShuffledChips = true;
					}
				}

				if (segmentUpdated) {
					shuffledPromptSegments[i] = {
						...segment,
						chips: updatedChips
					};
				}
			}
		}

		// Process negative prompt segments
		for (let i = 0; i < shuffledNegativePromptSegments.length; i++) {
			const segment = shuffledNegativePromptSegments[i];
			if (segment.chips && Object.keys(segment.chips).length > 0) {
				const updatedChips: Record<string, any> = {};
				let segmentUpdated = false;

				for (const [chipId, chipData] of Object.entries(segment.chips)) {
					const shuffled = shuffleChip(chipData);
					updatedChips[chipId] = shuffled;
					if (shuffled !== chipData) {
						segmentUpdated = true;
						hasShuffledChips = true;
					}
				}

				if (segmentUpdated) {
					shuffledNegativePromptSegments[i] = {
						...segment,
						chips: updatedChips
					};
				}
			}
		}

		// Build prompts array based on mode (single vs multi-prompt vs prompt-relay)
		let promptsArray: PromptPair[];
		const currentNumPrompts = presetVars[currentTab.selectedPreset || '']?.num_prompts || 1;
		const currentSegmentJoin = segmentJoinForPreset(currentTab.selectedPreset);

		// form_data sent to the backend; prompt-relay mode injects its timeline + global prompt
		let formDataForRequest: Record<string, unknown> = currentTab.formData;

		if (videoDirectorActive && videoDirectorCaps) {
			// Video Director mode: the structured multi-mode editor (tab.videoDirector)
			// is normalized then mapped to the backend wire contract and attached to
			// form_data.video_director; the pipeline reads it from there.
			const doc = normalizeDirectorValue(currentTab.videoDirector, videoDirectorCaps);
			const wireDoc = buildDirectorSubmission(doc, videoDirectorCaps);
			// A media entry may point at the form's own media-loader field(s)
			// (Stage B reference media) rather than embedding its own copy --
			// resolve those live, right before the request is built. The server
			// contract (form_data.video_director) never sees `form_ref`.
			const { doc: resolvedWireDoc, errors: formRefErrors } = dereferenceFormMediaRefs(wireDoc, currentTab.formData);
			if (formRefErrors.length > 0) {
				toasts.error(
					`Video Director references media that's no longer on the form: ${formRefErrors.join('; ')}`
				);
				return;
			}
			formDataForRequest = {
				...currentTab.formData,
				video_director: resolvedWireDoc
			};

			// A representative positive prompt so the standard validation/record path is satisfied.
			promptsArray = [{ positive: representativeDirectorPrompt(doc, videoDirectorCaps), negative: doc.negative_prompt || '' }];
		} else if (musicDirectorActive && musicDirectorCaps) {
			// Music Director mode: the structured composition editor (tab.musicDirector)
			// is normalized then mapped to the backend wire contract and attached to
			// form_data.music_director; the pipeline reads it from there. Unlike Video
			// Director there is no whole-form reference pool to dereference -- the
			// document's `references` are already the resolved shape.
			const doc = normalizeMusicDirectorValue(currentTab.musicDirector, musicDirectorCaps);
			const wireDoc = buildMusicDirectorSubmission(doc, musicDirectorCaps);
			formDataForRequest = {
				...currentTab.formData,
				music_director: wireDoc
			};

			// A representative positive prompt so the standard validation/record path is satisfied.
			promptsArray = [{ positive: doc.description, negative: '' }];
		} else if (promptRelayActive) {
			// Prompt Relay mode: prompts + duration live on the timeline editor (tab.promptRelay).
			// The pipeline reads them from form_data via get_form('custom', ['timeline'|'global_prompt']).
			const relay = currentTab.promptRelay;
			const segments = (relay?.timeline?.segments || [])
				.slice()
				.sort((a, b) => a.start - b.start);
			const globalPrompt = (relay?.global_prompt || '').trim();
			const joinedSegments = segments.map((s) => (s.text || '').trim()).filter(Boolean).join(' | ');

			formDataForRequest = {
				...currentTab.formData,
				global_prompt: globalPrompt,
				timeline: relay?.timeline ?? { duration: 5, fps: 24, segments: [] }
			};

			// A representative positive prompt so the standard validation/record path is satisfied.
			promptsArray = [{ positive: [globalPrompt, joinedSegments].filter(Boolean).join(' | '), negative: '' }];
		} else if (currentNumPrompts > 1 && currentTab.promptTabs && currentTab.promptTabs.length > 0) {
			// Multi-prompt mode: build array from all prompt tabs
			promptsArray = currentTab.promptTabs.slice(0, currentNumPrompts).map((promptTab) => {
				// Apply chip shuffling to each prompt tab
				let tabPromptSegments = [...(promptTab.promptSegments || [])];
				let tabNegativeSegments = [...(promptTab.negativePromptSegments || [])];

				// Process positive segments for this tab
				for (let i = 0; i < tabPromptSegments.length; i++) {
					const segment = tabPromptSegments[i];
					if (segment.chips && Object.keys(segment.chips).length > 0) {
						const updatedChips: Record<string, any> = {};
						let segmentUpdated = false;

						for (const [chipId, chipData] of Object.entries(segment.chips)) {
							const shuffled = shuffleChip(chipData);
							updatedChips[chipId] = shuffled;
							if (shuffled !== chipData) {
								segmentUpdated = true;
							}
						}

						if (segmentUpdated) {
							tabPromptSegments[i] = { ...segment, chips: updatedChips };
						}
					}
				}

				// Process negative segments for this tab
				for (let i = 0; i < tabNegativeSegments.length; i++) {
					const segment = tabNegativeSegments[i];
					if (segment.chips && Object.keys(segment.chips).length > 0) {
						const updatedChips: Record<string, any> = {};
						let segmentUpdated = false;

						for (const [chipId, chipData] of Object.entries(segment.chips)) {
							const shuffled = shuffleChip(chipData);
							updatedChips[chipId] = shuffled;
							if (shuffled !== chipData) {
								segmentUpdated = true;
							}
						}

						if (segmentUpdated) {
							tabNegativeSegments[i] = { ...segment, chips: updatedChips };
						}
					}
				}

				return {
					positive: tabPromptSegments.length > 0
						? resolvePromptSegments(tabPromptSegments, currentSegmentJoin)
						: promptTab.prompt || '',
					negative: tabNegativeSegments.length > 0
						? resolvePromptSegments(tabNegativeSegments, currentSegmentJoin)
						: promptTab.negativePrompt || ''
				};
			});
		} else {
			// Single prompt mode (legacy): use shuffled segments
			const mergedPrompt = shuffledPromptSegments.length > 0
				? resolvePromptSegments(shuffledPromptSegments, currentSegmentJoin)
				: currentTab.prompt;

			const mergedNegativePrompt = shuffledNegativePromptSegments.length > 0
				? resolvePromptSegments(shuffledNegativePromptSegments, currentSegmentJoin)
				: currentTab.negativePrompt;

			promptsArray = [{
				positive: mergedPrompt.trim(),
				negative: mergedNegativePrompt.trim()
			}];
		}

		// Validate at least one prompt has content (promptless modes skip this —
		// upscale/slow-motion/etc. legitimately submit an empty prompt).
		const hasValidPrompt = promptsArray.some(p => p.positive.trim().length > 0);
		if (!currentTab.selectedPreset || (!hasValidPrompt && !promptlessActive)) {
			return;
		}

		// No single-in-flight guard: the backend now queues generations, so any
		// tab can enqueue at any time (including a second generation from the
		// same tab while one is already pending/running).

		try {
			// A fresh submission attempt supersedes whatever field-validation errors
			// the last one left behind, regardless of how this attempt turns out.
			formValidationStore.clearAll(activeTabId);

			// Clear previous generation data (keep the outstanding queue — a new
			// enqueue from this tab must not drop generations already in flight)
			tabsStore.updateTab(activeTabId, {
				generation: {
					isGenerating: false,
					currentGeneration: null,
					currentProgress: null,
					pipeTimers: {},
					startedAt: null,
					totalTime: null,
					// Not reset here: `last` in the generation bar reads this and must
					// keep showing the previous run's duration through the next run.
					lastDurationMs: currentTab.generation.lastDurationMs,
					batchImages: [],
					batchVideos: [],
					batchAudios: [],
					batchMeshes: [],
					artifacts: [],
					workbenchIndex: 0,
					workbenchTotal: 0,
					queue: currentTab.generation.queue || [],
					// Only `promptsArray[0]` is ever treated as a template by the
					// backend expander (prompt_expansion.py) — capture it now so the
					// rendered-prompt artifact card can show what each `{a|b}`/`${var}`
					// resolved to, without drifting if the prompt is edited while this
					// generation is in flight.
					submittedPromptTemplate: promptsArray[0]
						? { positive: promptsArray[0].positive, negative: promptsArray[0].negative }
						: null
				}
			});

			// Definitions ride separately from the prompt text — see
			// GenerationRequest.variables (src/features/generation/dto.py) and
			// expander.py's _base_context(). Shared with generationOrchestrator.ts's
			// startGeneration() via buildVariablesPayload so the two request-assembly
			// implementations can't drift. This is also where shuffle-mode choice
			// variables get rolled for THIS Generate click — `variablesResult.rolls`
			// is persisted onto the tab in the success block below.
			const variablesResult = buildVariablesPayload(currentTab);

			const request: GenerationRequest = {
				preset_id: currentTab.selectedPreset,
				prompts: promptsArray,
				mode: currentTab.selectedMode ?? undefined,
				form_name: currentTab.selectedVariant ?? undefined,
				form_data: formDataForRequest,
				backend_id: currentTab.selectedBackendId ?? undefined,
				tag_ids: currentTab.autoTagIds?.length ? currentTab.autoTagIds : undefined,
				collection_ids: currentTab.autoCollectionIds?.length ? currentTab.autoCollectionIds : undefined,
				variables: variablesResult.variables,
				tab_id: activeTabId,
				source_prompt_id: currentTab.sourcePromptId ?? undefined,
				prompt_state: {
					prompt: currentTab.prompt,
					negativePrompt: currentTab.negativePrompt,
					promptSegments: currentTab.promptSegments,
					negativePromptSegments: currentTab.negativePromptSegments,
					promptTabs: currentTab.promptTabs,
					activePromptTab: currentTab.activePromptTab,
					promptRelay: currentTab.promptRelay,
					videoDirector: currentTab.videoDirector
				},
				segments: buildSegmentsPayload(currentTab, currentNumPrompts)
			};

			// Non-blocking: an undefined ${name} doesn't fail the generation, the
			// backend expander binds unknown_variable_value="" and it silently
			// expands to nothing (src/features/prompt/expander.py _base_context) —
			// surface it instead of letting a typo or a wiring gap vanish silently.
			// Checked against the actual WIRE map (request.variables), not just
			// `Object.keys(currentTab.variables)` — a choice variable with no valid
			// options resolves to nothing too, and should warn the same way.
			const undefinedVariables = findUndefinedVariableUsages(
				request.prompts?.flatMap((p) => [p.positive, p.negative]) || [],
				Object.keys(request.variables || {})
			);
			if (undefinedVariables.length > 0) {
				toasts.warning(
					undefinedVariables.length === 1
						? `Variable \${${undefinedVariables[0]}} has no value — it will expand to nothing.`
						: `Variables ${undefinedVariables.map((n) => `\${${n}}`).join(', ')} have no value — they will expand to nothing.`
				);
			}

			const response = await api.startGeneration(request);

			if (response.success && response.data) {
				const { generation_id, status, queue_position } = response.data;
				const isQueued = queue_position !== null && queue_position !== undefined;

				// Update tab with generation started (or queued)
				tabsStore.updateTab(activeTabId, {
					activeGenerationId: generation_id,
					generation: {
						...currentTab.generation,
						isGenerating: true,
						startedAt: Date.now(),
						totalTime: null,
						currentGeneration: {
							...status,
							id: generation_id,
							generation_id: generation_id,
							queue_position: queue_position ?? null
						},
						queue: [
							...(currentTab.generation.queue || []),
							{
								generation_id,
								queue_position: queue_position ?? null,
								status: isQueued ? 'pending' : 'running'
							}
						]
					},
					// Update segments with shuffled values so UI reflects what was sent
					...(hasShuffledChips ? {
						promptSegments: shuffledPromptSegments,
						negativePromptSegments: shuffledNegativePromptSegments
					} : {}),
					// Same idea for shuffle-mode choice variables: persist this click's
					// rolls as run state so `${name}` usage chips re-render showing the
					// pick — merge, don't replace, so a variable rolled
					// on an earlier click keeps its last roll until it's rolled again.
					...(Object.keys(variablesResult.rolls).length > 0 ? {
						variableRolls: { ...(currentTab.variableRolls || {}), ...variablesResult.rolls }
					} : {})
				});

				// Auto-switch to generation panel on mobile
				if ($isMobile) {
					setMobilePanel(2);
				}

				// Subscribe to WebSocket updates — a queued generation gets
				// `queue_update` messages the same way a running one gets
				// `generation_status`/etc, so subscribe unconditionally.
				if (ws) {
					ws.subscribe(generation_id, (message: WebSocketMessage) => {
						handleGenerationMessage(message);
					});
				}
			}
		} catch (error) {
			console.error('Failed to start generation:', error);

			// A 422 `form_validation_failed` body is a per-field problem the user
			// fixes inline in the form (see DynamicForm's `fieldErrors` prop) — no
			// generic toast for that case. Everything else (404 form_not_found,
			// template build errors, 500s, network errors) keeps the toast.
			const failure = classifyGenerationStartError(error);
			if (failure.kind === 'field_validation') {
				formValidationStore.setErrors(activeTabId, failure.fieldErrors);
			} else {
				toasts.error(failure.message);
			}

			tabsStore.updateTab(activeTabId, {
				activeGenerationId: null,
				generation: {
					...currentTab.generation,
					isGenerating: false
				}
			});
		}
	}

	async function cancelGeneration() {
		const currentGen = generation.currentGeneration;
		if (!currentGen || !currentGen.id) return;

		try {
			await api.cancelGeneration(currentGen.id);
			if (ws && currentGen.id) {
				ws.unsubscribe(currentGen.id);
			}

			tabsStore.updateTab(activeTabId, {
				activeGenerationId: null,
				generation: {
					...currentTab.generation,
					isGenerating: false,
					currentGeneration: null,
					currentProgress: null,
					queue: (currentTab.generation.queue || []).filter((q) => q.generation_id !== currentGen.id)
				}
			});
		} catch (error) {
			console.error('Failed to cancel generation:', error);
		}
	}

	async function clearGenerationQueue() {
		try {
			const response = await api.clearGenerationQueue(activeTabId);
			const cancelledIds = new Set(response.success ? response.data?.cancelled || [] : []);

			if (ws) {
				for (const id of cancelledIds) {
					ws.unsubscribe(id);
				}
			}

			const latestTab = $tabsStore.tabs.find((t) => t.id === activeTabId) || currentTab;
			const remainingQueue = (latestTab.generation.queue || []).filter(
				(q) => !cancelledIds.has(q.generation_id)
			);
			const currentGenCancelled =
				latestTab.generation.currentGeneration?.generation_id &&
				cancelledIds.has(latestTab.generation.currentGeneration.generation_id);

			tabsStore.updateTab(activeTabId, {
				...(currentGenCancelled ? { activeGenerationId: null } : {}),
				generation: {
					...latestTab.generation,
					queue: remainingQueue,
					...(currentGenCancelled
						? { isGenerating: false, currentGeneration: null, currentProgress: null }
						: {})
				}
			});
		} catch (error) {
			console.error('Failed to clear generation queue:', error);
		}
	}

	function handleGenerationMessage(message: WebSocketMessage) {
		dispatchGenerationMessage(message, {
			unsubscribe: (generationId: string) => ws?.unsubscribe(generationId)
		});
	}

	function addTab() {
		tabsStore.addTab();
	}

	function removeTab(tabId: string) {
		if (tabs.length > 1) {
			tabsStore.removeTab(tabId);
		}
	}

	function setActiveTab(tabId: string) {
		tabsStore.setActiveTab(tabId);
	}

	function switchToGeneratingTab() {
		if ($generatingTab) {
			tabsStore.setActiveTab($generatingTab.id);
		}
	}

	// Check if we can generate (has preset and prompt). Deliberately does NOT
	// gate on readiness (backend health / model availability) - a preset can
	// pass this check and still fail to start if, say, its backend just went
	// unhealthy; that failure surfaces through the classifyGenerationStartError
	// toast path below.
	$: {
		let hasPrompt = false;
		let noPromptReason = 'Missing prompt';

		if (promptlessActive) {
			// Promptless mode (upscale, slow-motion, …): no prompt is required.
			hasPrompt = true;
		} else if (videoDirectorActive && videoDirectorCaps) {
			const doc = normalizeDirectorValue(currentTab.videoDirector, videoDirectorCaps);
			const result = validateDirector(doc, videoDirectorCaps);
			hasPrompt = result.ok;
			noPromptReason = result.reasons[0] || noPromptReason;
		} else if (musicDirectorActive && musicDirectorCaps) {
			const doc = normalizeMusicDirectorValue(currentTab.musicDirector, musicDirectorCaps);
			const result = validateMusicDirector(doc, musicDirectorCaps);
			hasPrompt = result.ok;
			noPromptReason = result.reasons[0] || noPromptReason;
		} else if (promptRelayActive) {
			// Prompt Relay mode: prompts come from the timeline editor (segments or global prompt)
			const relay = currentTab.promptRelay;
			const hasSegmentText = (relay?.timeline?.segments || []).some(
				(s) => (s.text || '').trim().length > 0
			);
			const hasGlobal = (relay?.global_prompt || '').trim().length > 0;
			hasPrompt = hasSegmentText || hasGlobal;
		} else {
			const currentNumPrompts = presetVars[currentTab.selectedPreset || '']?.num_prompts || 1;

			if (currentNumPrompts > 1 && currentTab.promptTabs && currentTab.promptTabs.length > 0) {
				// Multi-prompt mode: check if any prompt tab has content
				hasPrompt = currentTab.promptTabs.some(tab => {
					const fromSegments = tab.promptSegments && tab.promptSegments.length > 0
						? resolvePromptSegments(tab.promptSegments).trim().length > 0
						: false;
					return fromSegments || (tab.prompt && tab.prompt.trim().length > 0);
				});
			} else {
				// Single prompt mode
				hasPrompt = currentTab.promptSegments && currentTab.promptSegments.length > 0
					? resolvePromptSegments(currentTab.promptSegments).trim().length > 0
					: !!(currentTab.prompt && currentTab.prompt.trim());
			}
		}

		canGenerate = !!currentTab.selectedPreset && hasPrompt;
		generateDisabledReason = canGenerate
			? undefined
			: !currentTab.selectedPreset
				? 'Select a preset to generate'
				: noPromptReason;
	}

	// Workbench event handlers
	function handleWorkbenchPrevious() {
		const newIndex = Math.max(0, generation.workbenchIndex - 1);
		tabsStore.updateTab(activeTabId, {
			generation: {
				...currentTab.generation,
				workbenchIndex: newIndex
			}
		});
	}

	function handleWorkbenchNext() {
		// The whole chain, not just images+videos: counting two of the four
		// buckets pinned the index at the last video, so an audio or mesh output
		// after them could never be reached with the next arrow.
		const totalItems = galleryTotal({
			images: generation.batchImages,
			videos: generation.batchVideos,
			audios: generation.batchAudios,
			meshes: generation.batchMeshes
		});
		const newIndex = Math.min(totalItems - 1, generation.workbenchIndex + 1);
		tabsStore.updateTab(activeTabId, {
			generation: {
				...currentTab.generation,
				workbenchIndex: newIndex
			}
		});
	}

	function handleWorkbenchHeightChange(event: CustomEvent<string>) {
		tabsStore.updateTab(activeTabId, {
			workbenchMaxHeight: event.detail
		});
	}

	function handleMoveToWorkbench(event: CustomEvent<{ item: any; index: number }>) {
		const { item, index } = event.detail;

		// One `current_*` channel per bucket. Only setting current_image /
		// current_video left an audio or mesh tile selecting an entry with no
		// media behind it at all.
		const kind = normalizeFileType(item?.file_type) || 'image';
		tabsStore.updateTab(activeTabId, {
			generation: {
				...currentTab.generation,
				workbenchIndex: index,
				currentGeneration: {
					...currentTab.generation.currentGeneration,
					current_image: kind === 'image' ? item.url : null,
					current_video: kind === 'video' ? item.url : null,
					current_audio: kind === 'audio' ? item : null,
					current_mesh: kind === 'mesh' ? item.url : null,
					file_type: kind
				}
			}
		});
	}

</script>

<!-- Main Layout Wrapper. Below md the shell's main reserves the fixed bottom
	tab bar (4rem + safe-area), so this page must size itself to what's left of
	the *visible* viewport — 100vh would overflow behind the bar. -->
<div class="h-[calc(100dvh_-_4rem_-_env(safe-area-inset-bottom))] md:h-dvh flex flex-col bg-canvas overflow-hidden">

	<TabBar />

	<!-- Keep lightweight tab wrappers, but mount the expensive editor only for the active tab. -->
	{#each tabs as tab (tab.id)}
		{@const tabHandlers = createTabHandlers(tab.id)}
		{@const tabModes = modesPerTab[tab.id] || []}
		{@const isActive = tab.id === activeTabId}
		<div class="flex-1 flex flex-col min-h-0" style="display: {isActive ? 'flex' : 'none'}">
			{#if isActive}

			{#snippet noSelectionState(mobile: boolean)}
				<!-- Two dead ends collapse into this one snippet: an empty preset list
					(nothing installed/assigned/usable — explained via readiness, role-aware)
					vs. presets existing but nothing chosen yet (today's plain hint). -->
				<div class="w-full {mobile ? 'px-6' : 'max-w-md'} text-center">
					{#if presets.length === 0}
						<EmptyState
							icon={presetsEmptyState.showSetupLink ? 'settings' : 'cube'}
							title={presetsEmptyState.title}
							description={presetsEmptyState.action
								? `${presetsEmptyState.message} ${presetsEmptyState.action}`
								: presetsEmptyState.message}
							compact={mobile}
						>
							{#snippet actions()}
								{#if presetsEmptyState.showSetupLink}
									<Button variant="primary" size={mobile ? 'sm' : 'md'} href="/setup" icon="arrow-right">
										Go to Setup
									</Button>
								{/if}
							{/snippet}
						</EmptyState>
					{:else}
						{#if !mobile}
							<svg class="w-20 h-20 mx-auto text-fg-subtle mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
							</svg>
							<h3 class="text-xl font-semibold text-fg mb-2">Ready to generate</h3>
						{:else}
							<svg class="w-12 h-12 mx-auto text-fg-subtle mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
							</svg>
						{/if}
						<p class={mobile ? 'text-fg-muted text-sm' : 'text-fg-muted mb-4'}>
							{#if !tab.selectedPreset && !tab.selectedMode}
								Select a preset and mode to start generating
							{:else if !tab.selectedPreset}
								Select a preset to continue
							{:else}
								Select a mode to continue
							{/if}
						</p>
						{#if mobile}
							<button type="button" class="mt-3 text-xs text-fg-subtle underline" on:click={() => setMobilePanel(0)}>Go to Preset</button>
						{:else}
							<div class="inline-flex items-center gap-2 px-4 py-2 bg-surface-1/50 border border-line-strong/50 rounded-lg text-sm text-fg-muted">
								<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
									<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
								</svg>
								Use the selectors above to get started
							</div>
						{/if}
					{/if}
				</div>
			{/snippet}

			<!-- Main Content Area -->
			<div class="flex-1 min-h-0 overflow-hidden">
				{#if $isMobile}
					<!-- Mobile: Swipeable three-panel layout -->
					<div class="flex flex-col h-full">
						<!-- Panel indicator bar -->
						<div class="flex-shrink-0 flex items-center bg-surface-1 border-b border-line">
							<button
								type="button"
								class="flex-1 py-2.5 text-xs font-medium text-center transition-colors relative
									{mobilePanel === 0 ? 'text-signal' : 'text-fg-subtle'}"
								on:click={() => setMobilePanel(0)}
							>
								<span class="flex items-center justify-center gap-1.5">
									<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M6 13.5V3.75m0 9.75a1.5 1.5 0 010 3m0-3a1.5 1.5 0 000 3m0 3.75V16.5m12-3V3.75m0 9.75a1.5 1.5 0 010 3m0-3a1.5 1.5 0 000 3m0 3.75V16.5m-6-9V3.75m0 3.75a1.5 1.5 0 010 3m0-3a1.5 1.5 0 000 3m0 9.75V10.5" />
									</svg>
									Preset
								</span>
								{#if mobilePanel === 0}
									<span class="absolute bottom-0 left-0 right-0 h-0.5 bg-signal rounded-t"></span>
								{/if}
							</button>
							<button
								type="button"
								class="flex-1 py-2.5 text-xs font-medium text-center transition-colors relative
									{mobilePanel === 1 ? 'text-signal' : 'text-fg-subtle'}"
								on:click={() => setMobilePanel(1)}
							>
								<span class="flex items-center justify-center gap-1.5">
									<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" />
									</svg>
									Form
								</span>
								{#if mobilePanel === 1}
									<span class="absolute bottom-0 left-0 right-0 h-0.5 bg-signal rounded-t"></span>
								{/if}
							</button>
							<button
								type="button"
								class="flex-1 py-2.5 text-xs font-medium text-center transition-colors relative
									{mobilePanel === 2 ? 'text-signal' : 'text-fg-subtle'}"
								on:click={() => setMobilePanel(2)}
							>
								<span class="flex items-center justify-center gap-1.5">
									<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
									</svg>
									Generate
								</span>
								{#if mobilePanel === 2}
									<span class="absolute bottom-0 left-0 right-0 h-0.5 bg-signal rounded-t"></span>
								{/if}
							</button>
							<button
								type="button"
								class="flex-1 py-2.5 text-xs font-medium text-center transition-colors relative
									{mobilePanel === 3 ? 'text-signal' : 'text-fg-subtle'}"
								on:click={() => setMobilePanel(3)}
							>
								<span class="flex items-center justify-center gap-1.5">
									<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.75" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095 48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155" />
									</svg>
									LLM
								</span>
								{#if mobilePanel === 3}
									<span class="absolute bottom-0 left-0 right-0 h-0.5 bg-signal rounded-t"></span>
								{/if}
							</button>
						</div>

						<!-- Swipeable panels container -->
						<!-- touch-action: pan-y — vertical scrolling stays native, horizontal
							is ours, and fewer gestures escalate into a touchcancel. -->
						<div
							class="flex-1 overflow-hidden relative min-h-0 touch-pan-y"
							role="region"
							aria-label="Swipeable panels"
							bind:this={mobilePanelsContainer}
							on:touchstart={handleTouchStart}
							on:touchmove={handleTouchMove}
							on:touchend={handleTouchEnd}
							on:touchcancel={handleTouchCancel}
							on:scroll={resetSwipeContainerScroll}
						>
							<!-- Geometry is container-relative on purpose: 100vw panels drift
								out of alignment whenever the layout viewport ≠ container width
								(scrollbar gutter, iOS focus zoom). 25% of the 400%-wide track
								is exactly one container width. -->
							<div
								class="mobile-panels-track flex h-full"
								style="transform: translateX(calc({-mobilePanel * 25}% + {isSwiping ? touchDeltaX : 0}px));
									transition: {isSwiping ? 'none' : 'transform 0.3s ease'};"
							>
								<!-- Panel 0: Preset / Session / Mode -->
								<div class="mobile-panel overflow-y-auto">
									<div class="flex flex-col gap-3 p-4">
										<PresetControls
											{tab}
											{presets}
											{readiness}
											{isLoading}
											isReloading={isReloadingPreset && tab.id === activeTabId}
											availableModes={tabModes.map((m) => ({ id: m.name, label: m.label, variants: m.variants, sourcePlugin: m.source_plugin }))}
											onPresetChange={(id) => tabHandlers.handlePresetChange(id)}
											onModeChange={(mode) => tabHandlers.handleModeChange(mode)}
											onVariantChange={(variant) => tabHandlers.handleVariantChange(variant)}
											onReload={() => handlePresetReload(tab.id, tab.selectedPreset)}
										/>
										<SessionPill
											presetId={tab.selectedPreset}
											currentMode={tab.selectedMode}
											tabId={tab.id}
											presetVersion={presets.find((p: any) => p.id === tab.selectedPreset)?.version}
											availableModes={tabModes.map((m) => ({ id: m.name, variants: m.variants }))}
										/>
									</div>
								</div>

								<!-- Panel 1: Form -->
							<div class="mobile-panel overflow-y-auto">
								{#if tab.selectedPreset && tab.selectedMode}
							<div class="p-4 pb-32">
											<GenerationFormPane
												bind:formRef={dynamicFormRefs[tab.id]}
												{tab}
												{videoDirectorActive}
												onFormDataChange={(data) => handleFormDataChange(tab.id, data)}
											/>
										</div>
									{:else}
										<div class="flex items-center justify-center h-full">
											{@render noSelectionState(true)}
										</div>
									{/if}
								</div>

								<!-- Panel 2: Generation (Workbench + Prompts) -->
								<div class="mobile-panel overflow-y-auto">
									{#if tab.selectedPreset && tab.selectedMode}
										<div class="p-4 pb-32">
											{#if isActive}
												<GenerationWorkbenchPane
													{tab}
													onWorkbenchPrevious={handleWorkbenchPrevious}
													onWorkbenchNext={handleWorkbenchNext}
													onWorkbenchHeightChange={handleWorkbenchHeightChange}
													onMoveToWorkbench={handleMoveToWorkbench}
												/>
											{/if}

											{#if !promptlessActive}
												<PromptSection
													{tab}
													{tabHandlers}
													{promptRelayActive}
													{videoDirectorActive}
													{videoDirectorCaps}
													{musicDirectorActive}
													{musicDirectorCaps}
													{numPrompts}
													{negativePromptSupported}
													{negativeInert}
													spacingClass="mt-4"
												/>
											{/if}
										</div>
									{:else}
										<div class="flex items-center justify-center h-full">
											{@render noSelectionState(true)}
										</div>
									{/if}
								</div>

								<!-- Panel 3: LLM Chat -->
					<div class="mobile-panel flex flex-col overflow-hidden">
						<UnifiedAIChat />
					</div>
							</div>
						</div>
					</div>
				{:else if !tab.selectedPreset || !tab.selectedMode}
					<!-- Desktop: Placeholder when preset/mode not selected. The preset
						card still needs to be reachable before either is chosen, so it
						mounts in the same left settings-pane position GenerationPanels
						uses once a preset+mode exist (PresetHeader isn't nested
						inside GenerationPanels alone - it has to work before that
						component ever mounts too). -->
					<div class="flex h-full">
						<div
							class="flex-shrink-0 border-r border-line overflow-y-auto bg-surface-1/30 p-3"
							style="width: min({leftPanelWidth}px, 45vw)"
						>
							<PresetControls
								{tab}
								{presets}
								{readiness}
								{isLoading}
								isReloading={isReloadingPreset && tab.id === activeTabId}
								availableModes={tabModes.map((m) => ({ id: m.name, label: m.label, variants: m.variants, sourcePlugin: m.source_plugin }))}
								onPresetChange={(id) => tabHandlers.handlePresetChange(id)}
								onModeChange={(mode) => tabHandlers.handleModeChange(mode)}
								onVariantChange={(variant) => tabHandlers.handleVariantChange(variant)}
								onReload={() => handlePresetReload(tab.id, tab.selectedPreset)}
							/>
						</div>
						<div class="flex flex-1 items-center justify-center">
							{@render noSelectionState(false)}
						</div>
					</div>
				{:else}
					<!-- Desktop: Resizable Two-Panel Layout -->
					<GenerationPanels
						{tab}
						{tabHandlers}
						{promptRelayActive}
						{videoDirectorActive}
						{videoDirectorCaps}
						{musicDirectorActive}
						{musicDirectorCaps}
						{numPrompts}
						{negativePromptSupported}
						{negativeInert}
						promptless={promptlessActive}
						{isActive}
						{leftPanelWidth}
						{dynamicFormRefs}
						{presets}
						{readiness}
						{isLoading}
						isReloading={isReloadingPreset && tab.id === activeTabId}
						availableModes={tabModes.map((m) => ({ id: m.name, label: m.label, variants: m.variants, sourcePlugin: m.source_plugin }))}
						onFormDataChange={(data) => handleFormDataChange(tab.id, data)}
						onWorkbenchPrevious={handleWorkbenchPrevious}
						onWorkbenchNext={handleWorkbenchNext}
						onWorkbenchHeightChange={handleWorkbenchHeightChange}
						onMoveToWorkbench={handleMoveToWorkbench}
						onPresetChange={(id) => tabHandlers.handlePresetChange(id)}
						onModeChange={(mode) => tabHandlers.handleModeChange(mode)}
						onVariantChange={(variant) => tabHandlers.handleVariantChange(variant)}
						onReload={() => handlePresetReload(tab.id, tab.selectedPreset)}
					/>
				{/if}
			</div>

			<!-- Connection Status Warning -->
			{#if !isConnected}
				<div class="flex-shrink-0 px-4 py-2 bg-warning/10 border-t border-warning/25">
					<div class="flex items-center gap-3">
						<svg class="w-5 h-5 text-warning flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
							<path
								fill-rule="evenodd"
								d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
								clip-rule="evenodd"
							/>
						</svg>
						<div>
							<p class="text-sm font-medium text-warning">WebSocket disconnected. Real-time updates may not work.</p>
						</div>
					</div>
				</div>
				{/if}
			{/if}
		</div>
	{/each}

	<!-- Generation Panel - Desktop only -->
	{#if !$isMobile}
	<div class="flex-shrink-0 w-full">
		{#key currentTab.id}
		<GenerationPanel
			generation={generation}
			{isGenerating}
			onGenerate={startGeneration}
			onCancel={cancelGeneration}
			{canGenerate}
			disabledReason={generateDisabledReason}
			{generatingTabName}
			isActiveTabGenerating={$isActiveTabGenerating}
			onSwitchToGeneratingTab={switchToGeneratingTab}
			onClearQueue={clearGenerationQueue}
			presetId={currentTab.selectedPreset}
			currentMode={currentTab.selectedMode}
			tabId={currentTab.id}
			presetVersion={currentTabPresetVersion}
			availableModes={activeTabModes}
			on:generationcomplete={() => lastGenerationsRefreshSignal++}
		>
			<GenerationSettingsPanel
				slot="settings"
				tabId={currentTab.id}
				presetId={currentTab.selectedPreset ?? undefined}
				mode={currentTab.selectedMode ?? undefined}
				bind:autoTagIds={currentTab.autoTagIds}
				bind:autoCollectionIds={currentTab.autoCollectionIds}
				soundOnComplete={currentTab.soundOnComplete}
				soundOnError={currentTab.soundOnError}
			/>
			<LastGenerationsDrawer
				slot="lastGenerations"
				presetId={currentTab.selectedPreset}
				presetName={presets.find((p: any) => p.id === currentTab.selectedPreset)?.name}
				refreshSignal={lastGenerationsRefreshSignal}
			/>
		</GenerationPanel>
		{/key}
	</div>
	{/if}

	<!-- Mobile generation transport. Shown on the Form and Generate panels —
		the two places the user is when they want to fire or cancel a run.
		Anchored above the fixed bottom tab bar (4rem + safe-area). -->
	{#if $isMobile && (mobilePanel === 1 || mobilePanel === 2)}
		<div class="fixed bottom-[calc(4.75rem_+_env(safe-area-inset-bottom))] left-3 right-3 z-40 overflow-hidden rounded-xl border border-line-strong bg-surface-1 shadow-floating md:hidden">
			<div class="absolute inset-x-0 top-0 h-1 bg-surface-3 overflow-hidden">
				{#if isGenerating}
					{#if generation.currentProgress?.progress != null}
						<div class="h-full bg-accent" style="width: {generation.currentProgress.progress * 100}%; transition: width 150ms var(--ease-out-quart);"></div>
					{:else}
						<!-- The active stage hasn't reported a fraction yet (e.g. a cold
						     model load) - a sliding bar reads as "still working", a bar
						     stuck at 0% reads as hung. -->
						<div class="h-full w-1/3 bg-accent progress-indeterminate"></div>
					{/if}
				{/if}
			</div>
			<div class="flex h-16 items-center gap-3 px-3 pt-1">
				<span class="h-2.5 w-2.5 flex-shrink-0 rounded-full {isGenerating ? "bg-accent animate-pulse" : "bg-success"}"></span>
				<div class="min-w-0 flex-1">
					<p class="truncate text-sm font-medium text-fg">{isGenerating ? "Generating" : !canGenerate && generateDisabledReason ? "Can't generate yet" : "Ready to generate"}</p>
					<p class="truncate font-mono text-2xs tabular-nums text-fg-subtle">
						{#if isGenerating}
							{#if generation.currentProgress?.progress != null}
								{Math.round(generation.currentProgress.progress * 100)}%
							{:else}
								Working…
							{/if}
							{#if generation.queue?.length} · {generation.queue.length} {generation.queue.length === 1 ? "job" : "jobs"}{/if}
						{:else if !canGenerate && generateDisabledReason}
							{generateDisabledReason}
						{:else}
							Configure the prompt and settings
						{/if}
					</p>
				</div>
				<button
					type="button"
					on:click={isGenerating ? cancelGeneration : startGeneration}
					disabled={!isGenerating && !canGenerate}
					class="inline-flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full transition-colors active:scale-95 disabled:cursor-not-allowed disabled:opacity-40 {isGenerating ? "bg-danger-solid text-white" : "bg-accent text-accent-contrast"}"
					aria-label={isGenerating ? "Cancel generation" : !canGenerate && generateDisabledReason ? generateDisabledReason : "Generate"}
				>
					{#if isGenerating}
						<svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
					{:else}
						<svg class="h-5 w-5 translate-x-px" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5.8v12.4c0 .8.9 1.3 1.6.9l9.7-6.2a1 1 0 000-1.8L8.6 4.9A1 1 0 007 5.8z" /></svg>
					{/if}
				</button>
			</div>
		</div>
	{/if}
</div>

<style>
	/* Mobile swipeable panels */
	.mobile-panels-track {
		width: 400%;
		will-change: transform;
	}

	/* Indeterminate progress: no known fraction yet, so slide a fixed-width
	   segment instead of pinning the bar at 0% (reads as hung, not working). */
	.progress-indeterminate {
		animation: progress-indeterminate-slide 1.2s ease-in-out infinite;
	}

	@keyframes progress-indeterminate-slide {
		0% {
			transform: translateX(-100%);
		}
		100% {
			transform: translateX(300%);
		}
	}

	.mobile-panel {
		min-width: 25%;
		max-width: 25%;
		width: 25%;
		flex-shrink: 0;
		height: 100%;
	}

	/* Custom scrollbar for panels */
	:global(.overflow-y-auto) {
		scrollbar-width: thin;
		scrollbar-color: rgb(var(--line-strong)) transparent;
	}

	:global(.overflow-y-auto::-webkit-scrollbar) {
		width: 6px;
	}

	:global(.overflow-y-auto::-webkit-scrollbar-track) {
		background: transparent;
	}

	:global(.overflow-y-auto::-webkit-scrollbar-thumb) {
		background-color: rgb(var(--line-strong));
		border-radius: 3px;
	}

	:global(.overflow-y-auto::-webkit-scrollbar-thumb:hover) {
		background-color: rgb(var(--line-hover));
	}
</style>
