<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { tabsStore, activeTab, generatingTab, isActiveTabGenerating } from '$lib/stores/tabs';
	import type { PromptTabData, DirectorRunState, Tab } from '$lib/types/tabs';
	import { authStore } from '$lib/stores/auth';
	import { api, type GenerationRequest, type PromptPair } from '$lib/services/api';
	import type { GenerationQueueSnapshot } from '$lib/types/api';
	import { buildSegmentsPayload, buildVariablesPayload } from '$lib/utils/generationOrchestrator';
	import { findUndefinedVariableUsages } from '$lib/utils/promptVariables';
	import { buildSessionRestoreTabPatch } from '$lib/utils/sessionRestore';
	import {
		collectTabSessionData,
		isSessionGoneError,
		isSessionMissingResponse,
		normalizeSessionBaselineFormData,
		shouldRestoreTabSessionOnMount
	} from '$lib/utils/sessionTabState';
	import { WebSocketService, createGenerationSocket } from '$lib/services/websocket';
	import type { WebSocketMessage } from '$lib/services/websocket';
	import { dispatchGenerationMessage } from '$lib/stores/generation';
	import GenerationPanel from '$lib/components/GenerationPanel.svelte';
	import type DynamicForm from '$lib/components/DynamicForm.svelte';
	import TabBar from './components/TabBar.svelte';
	import GenerationPanels from './components/GenerationPanels.svelte';
	import PresetControls from './components/PresetControls.svelte';
	import StudioView from './components/studio/StudioView.svelte';
	import { resolveNegativeApplicability } from '$lib/generation/negativeApplied';
	import { reconcileTabGenerations } from '$lib/generation/restore/reconcile';
	import { toggleFloatingForm } from '$lib/generation/floatingForm';
	import { toggleFloatingWorkbench } from '$lib/generation/floatingWorkbench';
	let generationPanelRef: GenerationPanel | undefined;
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
	import { resolveDirectorCapabilities, normalizeDirectorValue, validateDirector, buildDirectorSubmission, representativeDirectorPrompt, dereferenceFormMediaRefs, seedDirectorPromptFromLegacyText } from '$lib/utils/videoDirector';
	import {
		directorShotInputIdentity,
		directorPredecessorShotId,
		directorPredecessorOutputKey,
		type DirectorGenerationContext
	} from '$lib/utils/directorInputIdentity';
	import { planDirectorSelection } from '$lib/utils/directorPlanner';
	import { runDirectorDependencyPlan, type DirectorShotSubmitOutcome, type DirectorShotTerminalOutcome } from '$lib/utils/directorDependencyRunner';
	import { resolvePredecessorFrame, type PredecessorOutputLike } from '$lib/utils/directorContinuation';
	import { peekGenerationOutputs, setGenerationUnsubscribeHandler } from '$lib/generation/messages/generationOutputs';
	import type { VideoDirectorWireDoc, VideoDirectorValue, DirectorMediaValue } from '$lib/types/videoDirector';
	import type { DirectorCapabilities } from '$lib/types/videoDirector';
	import { resolveMusicDirectorCapabilities, normalizeMusicDirectorValue, validateMusicDirector, buildMusicDirectorSubmission } from '$lib/utils/musicDirector';
	import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
	import { resolveVariant } from '$lib/utils/variants';
	import { isPromptlessMode } from '$lib/utils/promptlessMode';
	import { toasts } from '$lib/stores/toast';
	import { getBackends, type Backend } from '$lib/services/admin-api';
	import { buildActiveTabReuseUpdate } from '$lib/utils/historyReuse';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import { timeAgo } from '$lib/utils/relativeTime';
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

	// Video Director's Shot Console mirrors its own transient row-checkbox
	// selection up here (per tab id, since every tab keeps its own
	// ShotConsole instance mounted -- see GenerationPanels.svelte's per-tab
	// `{#each tabs as tab (tab.id)}`) via `onCheckedChange`; `startGeneration`
	// below reads the ACTIVE tab's entry to scope which shot(s) it submits.
	// Deliberately plain state, not persisted (PLAN.md §C W3: the checked set
	// is transient, unlike `directorRuns`).
	let directorCheckedByTab: Record<string, Set<string>> = {};
	function handleDirectorCheckedChange(tabId: string, checked: Set<string>) {
		directorCheckedByTab = { ...directorCheckedByTab, [tabId]: checked };
	}

	/** Cancel callbacks for every `waitForDirectorShotTerminal` promise
	 *  currently outstanding -- settled (as `'abandoned'`) and cleared on
	 *  `onDestroy` below, since this component's own WebSocket is torn down
	 *  there too and nothing would ever move a pending wait to a real
	 *  terminal state afterwards (see `waitForDirectorShotTerminal`). */
	const pendingDirectorShotWaiters = new Set<() => void>();

	/** One `DirectorRunState` per shot id a just-started generation covers
	 *  (PLAN.md §C W3) -- `inputsHash`/`predecessorRef` are captured from `doc`/
	 *  `runs` NOW, at submit time, so they reflect what was actually sent even
	 *  if the document (or the predecessor's own run) keeps changing while the
	 *  generation is in flight. `runs` is the freshest known runs map for
	 *  looking up a dependent shot's predecessor -- callers pass whatever they
	 *  already have in scope (a multi-shot submission's later shots must see
	 *  the entries an earlier shot in the SAME call just recorded).
	 *
	 * `shotIds` can itself contain BOTH a predecessor and its dependant --
	 * `submitOneDirectorWireDoc`'s chain/H3 branch submits a whole checked
	 * native-continuation span as ONE wire doc under ONE `generationId`
	 * (`submitVideoDirectorShots`'s `segmentRouting` branch). When that's the
	 * case the dependant's predecessorRef must stamp THIS SAME NEW
	 * `generationId`, never a lookup into `runs` -- that map is either still
	 * missing the predecessor's entry entirely (this span's first-ever
	 * render: `runs` predates this very call) or holds an OLDER generation id
	 * from a PRIOR render of the span (a rerender/Retry), either of which
	 * left the dependant reading `unverified`/`stale` right after a
	 * same-batch render that was actually fully continuous. `runs` is only
	 * ever consulted for a predecessor OUTSIDE this batch (the LTX timeline
	 * path submits one shot per call, so its predecessor is always outside
	 * `shotIds`; `directorDependencyRunner.ts`'s own gating guarantees such a
	 * predecessor already reads 'done' with a stable `generationId` by the
	 * time this runs). */
	function buildDirectorRunEntries(
		shotIds: string[],
		generationId: string,
		status: 'queued' | 'generating',
		doc: VideoDirectorValue,
		caps: DirectorCapabilities,
		formData: Record<string, unknown> | null | undefined,
		runs: Record<string, DirectorRunState> | null | undefined,
		generationContext: DirectorGenerationContext
	): Record<string, DirectorRunState> {
		const entries: Record<string, DirectorRunState> = {};
		const shotIdsInThisGeneration = new Set(shotIds);
		for (const shotId of shotIds) {
			const predecessorId = directorPredecessorShotId(doc, caps, shotId);
			let predecessorRef: DirectorRunState['predecessorRef'] = null;
			if (predecessorId) {
				if (shotIdsInThisGeneration.has(predecessorId)) {
					predecessorRef = { generationId, outputKey: directorPredecessorOutputKey(caps, predecessorId) };
				} else {
					const predecessorRun = runs?.[predecessorId];
					predecessorRef = predecessorRun
						? { generationId: predecessorRun.generationId, outputKey: directorPredecessorOutputKey(caps, predecessorId) }
						: null;
				}
			}
			entries[shotId] = {
				generationId,
				status,
				progress: null,
				finishedAt: null,
				posterUrl: null,
				inputsHash: directorShotInputIdentity(doc, shotId, { caps, formData, generationContext }),
				predecessorRef
			};
		}
		return entries;
	}

	/** The generation context a tab's own request actually submits under --
	 *  see `DirectorGenerationContext`'s own doc comment (directorInputIdentity.ts)
	 *  on why this is required scope for the shot input identity, not an
	 *  optional nicety. */
	function directorGenerationContextFor(tab: Tab): DirectorGenerationContext {
		return { presetId: tab.selectedPreset ?? null, variant: tab.selectedVariant ?? null, mode: tab.selectedMode ?? null };
	}

	/** Fresh per-generation-id output snapshot from every run `tabId` currently
	 *  knows about -- `resolvePredecessorFrame`'s `outputsById` (keyed by
	 *  GENERATION id, `directorContinuation.ts`'s own doc comment on why this
	 *  is tried before a run's persisted `posterUrl`). */
	function snapshotDirectorGenerationOutputs(
		runs: Record<string, DirectorRunState> | null | undefined
	): Record<string, PredecessorOutputLike> | null {
		if (!runs) return null;
		const byGenerationId: Record<string, PredecessorOutputLike> = {};
		for (const run of Object.values(runs)) {
			if (run.generationId) byGenerationId[run.generationId] = peekGenerationOutputs(run.generationId);
		}
		return byGenerationId;
	}

	/**
	 * Submits one Video Director wire doc (already built, form-ref-resolved
	 * by the caller is NOT assumed -- this does that too) as a real
	 * generation, and records `shotsForDoc`'s `directorRuns`/`directorRunLinks`
	 * once it's queued. Factored out of `submitVideoDirectorShots` so the
	 * LTX timeline path can submit shots one at a time, gated by
	 * `directorDependencyRunner.ts`, instead of firing every wire doc in one
	 * un-gated loop. Returns the generation id on success (so a dependant can
	 * be told exactly which generation to wait for -- `directorDependencyRunner.ts`'s
	 * own doc comment on why re-reading `directorRuns`' current status is not
	 * a substitute) or `{ ok: false }` on a validation or start failure --
	 * NEVER `void` either way, so the runner can tell "queued" apart from
	 * "never actually submitted" instead of treating a failed rerender as if
	 * it had succeeded (Codex review, 14:56 UTC).
	 *
	 * `predecessorRefOverride` -- when the caller is `directorDependencyRunner.ts`
	 * (an LTX shot submitted one at a time, `shotsForDoc` always length 1) --
	 * is the EXACT `{generationId, outputKey}` `resolvePredecessorFrame`
	 * already resolved to build THIS SAME `wireDoc`'s predecessor media
	 * (`runDirectorDependencyPlan`'s own doc comment on `submit`). Stamped
	 * onto that one entry in place of `buildDirectorRunEntries`' own `runs`
	 * lookup so the two can never disagree about which generation this
	 * request's media actually came from. `undefined` (the chain/H3 caller,
	 * `submitVideoDirectorShots`) leaves `buildDirectorRunEntries`' own
	 * derivation untouched.
	 */
	async function submitOneDirectorWireDoc(
		tabId: string,
		tab: Tab,
		doc: VideoDirectorValue,
		caps: DirectorCapabilities,
		wireDoc: VideoDirectorWireDoc,
		shotsForDoc: string[],
		predecessorRefOverride?: DirectorRunState['predecessorRef']
	): Promise<DirectorShotSubmitOutcome> {
		const { doc: resolvedDoc, errors } = dereferenceFormMediaRefs(wireDoc, tab.formData);
		if (errors.length > 0) {
			const reason = `Video Director references media that's no longer on the form: ${errors.join('; ')}`;
			toasts.error(reason);
			return { ok: false, reason };
		}
		const positive = resolvedDoc.segments[0]?.prompt ?? '';
		const negative = resolvedDoc.segments[0]?.negative_prompt ?? '';
		const variablesResult = buildVariablesPayload(tab);
		const request: GenerationRequest = {
			preset_id: tab.selectedPreset!,
			prompts: [{ positive, negative }],
			mode: tab.selectedMode ?? undefined,
			form_name: tab.selectedVariant ?? undefined,
			form_data: { ...tab.formData, video_director: resolvedDoc },
			backend_id: tab.selectedBackendId ?? undefined,
			tag_ids: tab.autoTagIds?.length ? tab.autoTagIds : undefined,
			collection_ids: tab.autoCollectionIds?.length ? tab.autoCollectionIds : undefined,
			variables: variablesResult.variables,
			tab_id: tab.id,
			source_prompt_id: tab.sourcePromptId ?? undefined,
			prompt_state: {
				prompt: tab.prompt,
				negativePrompt: tab.negativePrompt,
				promptSegments: tab.promptSegments,
				negativePromptSegments: tab.negativePromptSegments,
				promptTabs: tab.promptTabs,
				activePromptTab: tab.activePromptTab,
				promptRelay: tab.promptRelay,
				videoDirector: tab.videoDirector
			},
			segments: buildSegmentsPayload(tab, presetVars[tab.selectedPreset!]?.num_prompts || 1)
		};
		try {
			const response = await api.startGeneration(request);
			if (response.success && response.data) {
				const { generation_id, queue_position } = response.data;
				const isQueued = queue_position !== null && queue_position !== undefined;
				const liveTab = $tabsStore.tabs.find((t) => t.id === tabId) || tab;
				const runEntries = buildDirectorRunEntries(
					shotsForDoc,
					generation_id,
					isQueued ? 'queued' : 'generating',
					doc,
					caps,
					tab.formData,
					liveTab.directorRuns,
					directorGenerationContextFor(tab)
				);
				if (predecessorRefOverride !== undefined && shotsForDoc.length === 1) {
					const soleShotId = shotsForDoc[0];
					runEntries[soleShotId] = { ...runEntries[soleShotId], predecessorRef: predecessorRefOverride };
				}
				tabsStore.updateTab(tabId, {
					generation: {
						...liveTab.generation,
						queue: [
							...(liveTab.generation.queue || []),
							{ generation_id, queue_position: queue_position ?? null, status: isQueued ? 'pending' : 'running' }
						]
					},
					directorRuns: {
						...(liveTab.directorRuns || {}),
						...runEntries
					},
					directorRunLinks: {
						...(liveTab.directorRunLinks || {}),
						[generation_id]: shotsForDoc
					}
				});
				if (ws) {
					ws.subscribe(generation_id, (message: WebSocketMessage) => handleGenerationMessage(message));
				}
				return { ok: true, generationId: generation_id };
			}
			toasts.error('Shot failed to start.');
			return { ok: false, reason: 'Shot failed to start.' };
		} catch (error) {
			console.error('Failed to start Video Director shot generation:', error);
			toasts.error('Shot failed to start.');
			return { ok: false, reason: 'Shot failed to start.' };
		}
	}

	/**
	 * Resolves once the run under `generationId` -- specifically THAT
	 * generation, not "whichever run currently occupies `shotId`" -- reaches
	 * a terminal state, or is abandoned. Driven by the same WebSocket
	 * `generation_complete`/`generation_error` handling that writes
	 * `directorRuns` (complete.ts/error.ts), never polled. Only ever awaited
	 * by `directorDependencyRunner.ts` for a generation id it (or the caller
	 * seeding it via `presubmittedGenerationIds`) just submitted, so a
	 * pre-existing terminal status on subscribe (the common case -- most runs
	 * finish long before anything awaits them) resolves immediately.
	 *
	 * `'abandoned'` -- resolved exactly like `'failed'` by the runner, never
	 * left pending -- covers every way `generationId` will now NEVER report a
	 * terminal state under `shotId`: the tab was closed, the shot's run entry
	 * was removed, or the shot was resubmitted/superseded under a DIFFERENT
	 * generation id before this one ever finished (`directorRuns.ts`'s own
	 * `existing.generationId === generationId` guard means a stale
	 * generation's terminal event can never resurrect this run once
	 * superseded, so waiting on the OLD id would hang forever without this).
	 * Also settled as `'abandoned'` from `onDestroy` if the page itself is
	 * torn down first (`pendingDirectorShotWaiters`) -- this component's own
	 * WebSocket disconnects there too, so nothing would ever arrive to
	 * resolve it for real.
	 */
	function waitForDirectorShotTerminal(tabId: string, shotId: string, generationId: string): Promise<DirectorShotTerminalOutcome> {
		return new Promise((resolve) => {
			let unsubscribe: () => void = () => {};
			let settled = false;
			const settle = (outcome: DirectorShotTerminalOutcome) => {
				if (settled) return;
				settled = true;
				pendingDirectorShotWaiters.delete(cancel);
				resolve(outcome);
				// `unsubscribe` isn't assigned yet the first time this runs
				// (subscribe() invokes synchronously with the current value) --
				// defer past that assignment instead of unsubscribing inline.
				Promise.resolve().then(() => unsubscribe());
			};
			const cancel = () => settle('abandoned');
			pendingDirectorShotWaiters.add(cancel);
			unsubscribe = tabsStore.subscribe((state) => {
				const liveTab = state.tabs.find((t) => t.id === tabId);
				if (!liveTab) {
					settle('abandoned'); // tab closed -- no generationId will ever report here again
					return;
				}
				const run = liveTab.directorRuns?.[shotId];
				if (!run || run.generationId !== generationId) {
					settle('abandoned'); // run cleared, or superseded by a different resubmission
					return;
				}
				if (run.status === 'done' || run.status === 'failed') settle(run.status);
			});
		});
	}

	/**
	 * Runs `shotsToSubmit` (already dependency-ordered, e.g. from
	 * `planDirectorSelection`) through `runDirectorDependencyPlan`, wiring it
	 * to this component's real submission (`submitOneDirectorWireDoc`) and
	 * real WebSocket-driven completion (`waitForDirectorShotTerminal`) --
	 * the ONE place either the contextual Retry/span path
	 * (`submitVideoDirectorShots`) or the ordinary Generate button's
	 * multi-shot follow-up (`startGeneration`) hands shots to the dependency
	 * runner, so both go through the identical handoff rather than two
	 * separately-maintained copies of it.
	 *
	 * `presubmittedGenerationIds` lets a caller that already submitted one of
	 * these shots through some OTHER mechanism (the ordinary Generate
	 * button's PRIMARY shot, which keeps its own tab-initializing submission
	 * path unchanged) tell the runner which generation id that shot is
	 * running under, so a dependant of it still waits on the real thing
	 * instead of being resubmitted here. `shotNumberOffset` keeps a "Shot N:"
	 * toast numbered against the FULL film when `shotsToSubmit` itself only
	 * covers a tail of it (the primary shot is never in `shotsToSubmit` here,
	 * so its own count is folded into the offset instead).
	 */
	async function submitDirectorTimelinePlan(
		tabId: string,
		tab: Tab,
		doc: VideoDirectorValue,
		caps: DirectorCapabilities,
		shotsToSubmit: string[],
		options: { presubmittedGenerationIds?: Record<string, string> | null; shotNumberOffset?: number } = {}
	): Promise<void> {
		if (shotsToSubmit.length === 0) return;
		const shotNumberOffset = options.shotNumberOffset ?? 0;
		const multi = shotNumberOffset > 0 || shotsToSubmit.length > 1;
		await runDirectorDependencyPlan(
			shotsToSubmit,
			doc,
			caps,
			{
				getRuns: () => $tabsStore.tabs.find((t) => t.id === tabId)?.directorRuns,
				getOutputs: () => snapshotDirectorGenerationOutputs($tabsStore.tabs.find((t) => t.id === tabId)?.directorRuns),
				submit: (shotId, predecessorFrame, predecessorRef) => {
					const liveTab = $tabsStore.tabs.find((t) => t.id === tabId) || tab;
					const predecessorFrames = predecessorFrame ? { [shotId]: predecessorFrame } : undefined;
					const wireDocs = buildDirectorSubmission(doc, caps, new Set([shotId]), predecessorFrames);
					if (wireDocs.length === 0) return Promise.resolve({ ok: false, reason: 'Nothing to submit' });
					return submitOneDirectorWireDoc(tabId, liveTab, doc, caps, wireDocs[0], [shotId], predecessorRef);
				},
				waitForTerminal: (shotId, generationId) => waitForDirectorShotTerminal(tabId, shotId, generationId),
				onBlocked: (shotId, reason) => {
					const index = shotsToSubmit.indexOf(shotId);
					const shotNumber = shotNumberOffset + index + 1;
					toasts.error(multi ? `Shot ${shotNumber}: ${reason}` : reason);
				}
			},
			options.presubmittedGenerationIds
		);
	}

	/**
	 * Submits exactly `shotIds` (in the order given) for `tabId`'s Video
	 * Director document -- backs the Shot Console's two contextual generate
	 * actions (PLAN.md §C W3): a failed row's Retry (one id) and a broken
	 * join's "Generate previous + this shot" (the contiguous span). Unlike
	 * the main Generate button (`startGeneration` above), this does NOT reset
	 * the tab's generation/workbench state -- it's a small, targeted
	 * resubmission that must not disturb other shots' already-displayed
	 * posters or the rest of the tab's generation UI.
	 */
	async function submitVideoDirectorShots(tabId: string, shotIds: string[]): Promise<void> {
		const tab = $tabsStore.tabs.find((t) => t.id === tabId);
		if (!tab || !tab.selectedPreset || shotIds.length === 0) return;
		const caps = resolveDirectorCapabilities(presetVars[tab.selectedPreset]?.video_director, tab.selectedMode);
		if (!caps) return;

		const doc = normalizeDirectorValue(tab.videoDirector, caps);
		// Gated to exactly the requested shots (Retry's one id, or a broken
		// join's contiguous span) -- an unrelated shot's own problems must
		// never block this targeted resubmission (directorPlanner.ts).
		const plan = planDirectorSelection(doc, caps, tab.directorRuns, shotIds);
		if (plan.blockingReasons.length > 0) {
			toasts.error(plan.blockingReasons[0] || 'Video Director is not ready to generate.');
			return;
		}

		const targetShotIds = plan.shotsToSubmit;
		if (targetShotIds.length === 0) return;

		if (caps.segmentRouting) {
			// Wan/H3 routed chain -- ONE generation covers every targeted shot
			// at once; its own continuation is server-side, in-process, within
			// that single generation, so there is nothing to wait on between
			// shots here (unlike the LTX timeline branch below).
			const wireDocs = buildDirectorSubmission(doc, caps, new Set(shotIds));
			if (wireDocs.length === 0) return;
			await submitOneDirectorWireDoc(tabId, tab, doc, caps, wireDocs[0], targetShotIds);
			return;
		}

		// LTX timeline -- one generation PER shot, in dependency order. A
		// continuation shot whose own predecessor is ALSO in this batch must
		// wait for that predecessor's generation to actually finish (and
		// inherit ITS resolved output) before submitting -- see
		// directorDependencyRunner.ts.
		await submitDirectorTimelinePlan(tabId, tab, doc, caps, targetShotIds);
	}

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

	let mounted = false;
	let isLoading = false;
	let canGenerate = false;
	// First reason `canGenerate` is false — surfaced on the generation bar's
	// mark/subline (GenerationPanel) instead of leaving a disabled Generate
	// button with no explanation.
	let generateDisabledReason: string | undefined;
	let isReloadingPreset = false;
	// Guards against overlapping restore passes on rapid connect/disconnect
	// flapping -- NOT a one-shot: a genuine reconnect (the connection drops
	// and comes back later) must re-run reconciliation, since a generation
	// could have finished, failed, or a Director shot could have been
	// resubmitted while this client was disconnected.
	let restoreInFlight = false;
	// Retired in onDestroy before the socket disconnects, so a late restore
	// response from a torn-down page writes and subscribes nothing.
	const restoreController = new AbortController();

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
	$: currentTabPresetEngine = presets.find((p: any) => p.id === currentTab.selectedPreset)?.engine;
	// Set by BackendPicker (inside the settings drawer) once it knows whether
	// the current preset's engine has more than one enabled backend - gates
	// the generation bar's "Runs on <backend> — <reason>" pre-flight line,
	// which would otherwise be noise when there's no real choice to explain.
	let currentTabHasMultipleBackends = false;
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
		// tabsStore.removeTab has no per-call context to unsubscribe an
		// orphaned generation's WebSocket subscription (it's called directly
		// from the tab bar's close button and this page's close-tab
		// keybinding, sharing no caller-supplied `deps`) -- registered once
		// here instead, cleared in onDestroy below.
		setGenerationUnsubscribeHandler((generationId) => ws?.unsubscribe(generationId));
		ws.onConnectionChange((connected) => {
			isConnected = connected;
			if (connected && !restoreInFlight) {
				restoreInFlight = true;
				restoreGenerations().finally(() => {
					restoreInFlight = false;
				});
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
		keybindingsStore.registerHandler('toggle_workbench_panel', () => {
			tabsStore.updateTab(activeTabId, { workbenchCollapsed: !currentTab.workbenchCollapsed });
		});
		keybindingsStore.registerHandler('toggle_last_generations', () => {
			generationPanelRef?.toggleDrawer('lastGenerations');
		});
		keybindingsStore.registerHandler('toggle_floating_form', () => {
			tabsStore.updateTab(activeTabId, toggleFloatingForm(currentTab));
		});
		keybindingsStore.registerHandler('toggle_floating_workbench', () => {
			tabsStore.updateTab(activeTabId, toggleFloatingWorkbench(currentTab));
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
			// tabsStore is module-scope, so SPA navigation remounts this page without
			// resetting it: a tab that already has a session baseline holds a live draft
			// that must not be overwritten. Only a full reload (baseline undefined after
			// localStorage rehydration) hydrates from the server.
			if (!shouldRestoreTabSessionOnMount(tab)) return;
			// shouldRestoreTabSessionOnMount already guarantees both are set; narrow
			// locally so the compiler sees it too.
			const selectedSessionId = tab.selectedSessionId!;
			const selectedMode = tab.selectedMode!;

			try {
				const response = await api.getSessionById(selectedSessionId);
				if (response.success && response.data) {
					const session = response.data;
					const modeData = session.data[selectedMode];

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
				} else if (isSessionMissingResponse(response)) {
					console.warn(`[TabRestore] Session for tab ${tab.name} no longer exists, clearing the link.`);
					tabsStore.updateTab(tab.id, {
						selectedSessionId: null,
						savedSessionSignature: null,
						sessionBaselineAwaitingFormNormalization: false
					});
				}
			} catch (error) {
				// A thrown HTTP 404 proves the session is gone; any other failure (network
				// error, backend still booting) must not destroy the tab's saved link -
				// the next successful loadSessions binds it back once reachable.
				if (isSessionGoneError(error)) {
					console.error(`[TabRestore] Failed to restore session for tab ${tab.name}:`, error);
					tabsStore.updateTab(tab.id, {
						selectedSessionId: null,
						savedSessionSignature: null,
						sessionBaselineAwaitingFormNormalization: false
					});
				} else {
					console.warn(`[TabRestore] Backend unreachable while restoring session for tab ${tab.name}, keeping the saved link:`, error);
				}
			}
		}));
	}

	// Single restore/reconcile pass for every tab, run on connect AND on every
	// reconnect (see the `restoreInFlight` re-entrancy guard at the call
	// site -- deliberately not a one-shot). Fetches the live backend queue
	// snapshot ONCE and folds each tab's pending/running ids into the SAME
	// reconciliation pass
	// as its persisted activeGenerationId/directorRuns/directorRunLinks --
	// deliberately not a second, independent merge: reconcileTabGenerations
	// re-confirms every id (persisted or snapshot-discovered) against its own
	// authoritative status lookup, so a stale/delayed snapshot claiming an id
	// is still pending/running can never resurrect a run reconciliation (or a
	// live event racing it) already resolved as terminal, and no id is ever
	// subscribed twice from two independent restore passes.
	async function restoreGenerations() {
		const currentTabs = $tabsStore.tabs;
		let snapshot: GenerationQueueSnapshot | null = null;
		try {
			const response = await api.getGenerationQueue();
			if (response.success && response.data) snapshot = response.data;
		} catch (error) {
			console.warn('[RestoreGenerations] Could not fetch the live queue snapshot:', error);
		}

		await Promise.all(
			currentTabs.map((tab) => {
				const extraCandidateIds = snapshot
					? [
							...snapshot.pending.filter((p) => p.tab_id === tab.id).map((p) => p.generation_id),
							...snapshot.running.filter((r) => r.tab_id === tab.id).map((r) => r.generation_id)
						]
					: [];
				return reconcileTabGenerations(tab.id, api, tabsStore, {
					extraCandidateIds,
					signal: restoreController.signal,
					onSubscribe: (generationId) => {
						ws?.subscribe(generationId, (message: WebSocketMessage) => {
							handleGenerationMessage(message);
						});
					},
					unsubscribe: (generationId) => ws?.unsubscribe(generationId)
				});
			})
		);
	}

	onDestroy(() => {
		restoreController.abort();
		setGenerationUnsubscribeHandler(null);
		if (ws) {
			ws.disconnect();
		}
		// Settle every outstanding Video Director dependency wait as
		// 'abandoned' -- `ws.disconnect()` above means no further terminal
		// WebSocket event will ever arrive to resolve one for real.
		for (const cancel of pendingDirectorShotWaiters) cancel();
		pendingDirectorShotWaiters.clear();
		// Unregister generate-context keybinding handlers
		keybindingsStore.unregisterHandler('start_generation');
		keybindingsStore.unregisterHandler('new_tab');
		keybindingsStore.unregisterHandler('close_tab');
		keybindingsStore.unregisterHandler('toggle_left_panel');
		keybindingsStore.unregisterHandler('toggle_workbench_panel');
		keybindingsStore.unregisterHandler('toggle_floating_form');
		keybindingsStore.unregisterHandler('toggle_floating_workbench');
		keybindingsStore.unregisterHandler('toggle_last_generations');
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

	// Lazily fetched + cached on first reuse click — only needed to resolve
	// whether a generation's original backend is still around (mirrors the
	// history page's own loadAvailableBackends).
	let availableBackends: Backend[] | null = null;
	async function loadAvailableBackends(): Promise<Backend[]> {
		if (availableBackends) return availableBackends;
		try {
			const response = await getBackends();
			availableBackends = response.data ?? [];
		} catch (error) {
			console.error('Failed to load backends for generation reuse:', error);
			availableBackends = [];
		}
		return availableBackends;
	}

	// "Reuse in this tab" from the last-generations drawer — applies a past
	// generation's preset/mode/form/prompt/seed onto the ACTIVE tab in place
	// (as opposed to the history page's reuse, which opens a new tab). A
	// preset switch included in the same `updateTab` call is picked up by the
	// per-tab mode-manifest effect above exactly as it is for a freshly
	// created tab, so the form re-renders against the new preset's schema.
	async function handleReuseInActiveTab(generation: GenerationHistoryItem) {
		if (!generation.preset_id) return;
		const tab = currentTab;
		const backends = await loadAvailableBackends();
		const { tabData, backendUnavailable, presetChanged } = buildActiveTabReuseUpdate(
			generation,
			tab,
			backends
		);

		if (presetChanged) {
			// Drop the stale mode manifest so the mode dropdown doesn't flash the
			// old preset's modes while the new preset's load — same cleanup
			// handlePresetChange does on a manual preset pick.
			delete modesPerTab[tab.id];
			delete modesPresetPerTab[tab.id];
			modesPerTab = { ...modesPerTab };
			modesPresetPerTab = { ...modesPresetPerTab };
		}

		tabsStore.updateTab(tab.id, tabData);

		if (backendUnavailable) {
			toasts.info('Original backend is no longer available — using the default backend.');
		}
		toasts.success(`Reused generation from ${timeAgo(generation.created_at)}`);
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

		// "One clip = one generation" still holds (PLAN.md §B): a chain doc,
		// or a single-shot timeline/t2v/i2v/flf doc, is exactly one wire doc,
		// but a multi-shot LTX film is N -- `directorRemainingShotIds` carries
		// the shot ids of the follow-up submissions after the primary one
		// below, submitted through `submitDirectorTimelinePlan` (the SAME
		// dependency-aware handoff `submitVideoDirectorShots` uses) rather
		// than pre-built here: a later shot's wire doc can only be built once
		// its own predecessor's resolved output is known, which isn't true
		// yet at this point in the function for anything but the primary.
		let primaryDirectorShotIds: string[] = [];
		let directorRemainingShotIds: string[] = [];
		let directorValueForRuns: VideoDirectorValue | null = null;

		if (videoDirectorActive && videoDirectorCaps) {
			// Video Director mode: the structured multi-mode editor (tab.videoDirector)
			// is normalized then mapped to the backend wire contract and attached to
			// form_data.video_director; the pipeline reads it from there.
			const doc = normalizeDirectorValue(currentTab.videoDirector, videoDirectorCaps);
			directorValueForRuns = doc;
			// The console's own transient row-checkbox selection (ShotConsole,
			// mirrored up via onCheckedChange) -- empty means "the whole film",
			// same as before the console had checkboxes at all.
			const directorChecked = directorCheckedByTab[activeTabId] ?? new Set<string>();
			// Same scoped gate as the Generate button's own readiness check
			// above (directorPlanner.ts) -- defends against a stale `canGenerate`
			// (e.g. a selection change that hasn't re-run the reactive block yet).
			const plan = planDirectorSelection(doc, videoDirectorCaps, currentTab.directorRuns, directorChecked);
			if (plan.blockingReasons.length > 0) {
				toasts.error(plan.blockingReasons[0] || 'Video Director is not ready to generate.');
				return;
			}
			const isChainDoc = videoDirectorCaps.segmentRouting;
			const targetShotIds = plan.shotsToSubmit;

			let wireDoc: VideoDirectorWireDoc;
			if (isChainDoc) {
				// Wan/H3 routed chain -- ONE generation covers every targeted shot
				// at once; its own continuation is server-side, in-process,
				// within that single generation, so there is no "remaining
				// shots" follow-up for a chain doc at all.
				wireDoc = buildDirectorSubmission(doc, videoDirectorCaps, directorChecked)[0];
				primaryDirectorShotIds = targetShotIds;
			} else {
				// LTX timeline -- one generation PER shot. The primary is
				// always `targetShotIds[0]` (the film's own shot order, which
				// `planDirectorSelection` already guarantees puts every
				// predecessor before its dependant), so if IT continues from a
				// predecessor, that predecessor can only be OUTSIDE this
				// selection -- already done (directorPlanner.ts's own
				// "predecessorDone" gate above already required that, or this
				// plan would have been blocked). Resolve it here so the primary
				// shot's own request carries it too, instead of only ever fixing
				// this for `submitVideoDirectorShots`'s contextual path.
				const primaryShotId = targetShotIds[0];
				directorRemainingShotIds = targetShotIds.slice(1);
				const predecessorId = directorPredecessorShotId(doc, videoDirectorCaps, primaryShotId);
				let predecessorFrame: DirectorMediaValue | null = null;
				if (predecessorId) {
					const resolved = resolvePredecessorFrame(
						doc,
						videoDirectorCaps,
						primaryShotId,
						currentTab.directorRuns,
						snapshotDirectorGenerationOutputs(currentTab.directorRuns)
					);
					if (!resolved.ok) {
						toasts.error(resolved.reason);
						return;
					}
					predecessorFrame = resolved.media;
				}
				wireDoc = buildDirectorSubmission(
					doc,
					videoDirectorCaps,
					new Set([primaryShotId]),
					predecessorFrame ? { [primaryShotId]: predecessorFrame } : undefined
				)[0];
				primaryDirectorShotIds = [primaryShotId];
			}
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
					routingBackend: null,
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
				const { generation_id, status, queue_position, backend } = response.data;
				const isQueued = queue_position !== null && queue_position !== undefined;

				// Update tab with generation started (or queued)
				tabsStore.updateTab(activeTabId, {
					activeGenerationId: generation_id,
					generation: {
						...currentTab.generation,
						isGenerating: true,
						startedAt: Date.now(),
						totalTime: null,
						routingBackend: backend ?? null,
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
					} : {}),
					// Per-shot Video Director run tracking (PLAN.md §C W3) -- only
					// when this submission actually covered shot(s) (Video Director
					// active and the film has at least one shot, always true once
					// `videoDirectorActive` since a document always has ≥1 shot).
					...(directorValueForRuns && videoDirectorCaps && primaryDirectorShotIds.length > 0
						? {
								directorRuns: {
									...(currentTab.directorRuns || {}),
									...buildDirectorRunEntries(
										primaryDirectorShotIds,
										generation_id,
										isQueued ? 'queued' : 'generating',
										directorValueForRuns,
										videoDirectorCaps,
										currentTab.formData,
										currentTab.directorRuns,
										directorGenerationContextFor(currentTab)
									)
								},
								directorRunLinks: {
									...(currentTab.directorRunLinks || {}),
									[generation_id]: primaryDirectorShotIds
								}
							}
						: {})
				});

				// Subscribe to WebSocket updates — a queued generation gets
				// `queue_update` messages the same way a running one gets
				// `generation_status`/etc, so subscribe unconditionally.
				if (ws) {
					ws.subscribe(generation_id, (message: WebSocketMessage) => {
						handleGenerationMessage(message);
					});
				}

				// Multi-shot LTX film: shot 1 above already enqueued as the primary
				// generation (tab state reset, `submittedPromptTemplate`, etc., all
				// unchanged) -- every remaining shot goes through the SAME
				// dependency-aware handoff `submitVideoDirectorShots`'s contextual
				// path uses (`submitDirectorTimelinePlan`), seeded with the
				// primary's own generation id so a remaining shot that continues
				// from it waits on THIS run, never resubmits it, and is blocked
				// rather than started on stale history if it failed or was
				// abandoned.
				if (directorValueForRuns && videoDirectorCaps && directorRemainingShotIds.length > 0 && primaryDirectorShotIds[0]) {
					await submitDirectorTimelinePlan(
						activeTabId,
						currentTab,
						directorValueForRuns,
						videoDirectorCaps,
						directorRemainingShotIds,
						{ presubmittedGenerationIds: { [primaryDirectorShotIds[0]]: generation_id }, shotNumberOffset: 1 }
					);
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
			// Scoped to the console's own checked-row selection (empty = the
			// whole film) -- an unselected shot's own problems must never block
			// a valid selected one (directorPlanner.ts).
			const directorChecked = directorCheckedByTab[currentTab.id] ?? new Set<string>();
			const plan = planDirectorSelection(doc, videoDirectorCaps, currentTab.directorRuns, directorChecked);
			hasPrompt = plan.blockingReasons.length === 0;
			noPromptReason = plan.blockingReasons[0] || noPromptReason;
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

			{#snippet noSelectionState()}
				<!-- Two dead ends collapse into this one snippet: an empty preset list
					(nothing installed/assigned/usable — explained via readiness, role-aware)
					vs. presets existing but nothing chosen yet (today's plain hint).
					Desktop-only since StudioView replaced the mobile carousel — mobile
					handles "nothing selected" inline (disabled shutter + reason). -->
				<div class="w-full max-w-md text-center">
					{#if presets.length === 0}
						<EmptyState
							icon={presetsEmptyState.showSetupLink ? 'settings' : 'cube'}
							title={presetsEmptyState.title}
							description={presetsEmptyState.action
								? `${presetsEmptyState.message} ${presetsEmptyState.action}`
								: presetsEmptyState.message}
						>
							{#snippet actions()}
								{#if presetsEmptyState.showSetupLink}
									<Button variant="primary" size="md" href="/setup" icon="arrow-right">
										Go to Setup
									</Button>
								{/if}
							{/snippet}
						</EmptyState>
					{:else}
						<svg class="w-20 h-20 mx-auto text-fg-subtle mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
						</svg>
						<h3 class="text-xl font-semibold text-fg mb-2">Ready to generate</h3>
						<p class="text-fg-muted mb-4">
							{#if !tab.selectedPreset && !tab.selectedMode}
								Select a preset and mode to start generating
							{:else if !tab.selectedPreset}
								Select a preset to continue
							{:else}
								Select a mode to continue
							{/if}
						</p>
						<div class="inline-flex items-center gap-2 px-4 py-2 bg-surface-1/50 border border-line-strong/50 rounded-lg text-sm text-fg-muted">
							<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
							</svg>
							Use the selectors above to get started
						</div>
					{/if}
				</div>
			{/snippet}

			<!-- Main Content Area -->
			<div class="flex-1 min-h-0 overflow-hidden">
				{#if $isMobile}
					<StudioView
						{tab}
						{tabHandlers}
						{presets}
						{readiness}
						{isLoading}
						isReloadingPreset={isReloadingPreset && tab.id === activeTabId}
						{tabModes}
						{startGeneration}
						{cancelGeneration}
						{canGenerate}
						{generateDisabledReason}
						{promptRelayActive}
						{videoDirectorActive}
						{videoDirectorCaps}
						{musicDirectorActive}
						{musicDirectorCaps}
						{numPrompts}
						{negativePromptSupported}
						{negativeInert}
						{promptlessActive}
						{dynamicFormRefs}
						onFormDataChange={(data) => handleFormDataChange(tab.id, data)}
						onReload={() => handlePresetReload(tab.id, tab.selectedPreset)}
						onMoveToWorkbench={handleMoveToWorkbench}
					/>
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
						<div class="flex flex-1 items-center justify-center pb-[var(--dock-height)]">
							{@render noSelectionState()}
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
						directorRuns={tab.directorRuns}
						onDirectorCheckedChange={(checked) => handleDirectorCheckedChange(tab.id, checked)}
						onDirectorGenerateShots={(shotIds) => submitVideoDirectorShots(tab.id, shotIds)}
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
				<div class="flex-shrink-0 px-4 py-2 bg-warning/10 border-t border-warning/25 mb-[var(--dock-height)]">
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

	<!-- Generation Panel - Desktop only. `.generation-panel` (maintainer ruling)
		keeps the pre-port docked-bar container: fixed, full-width, flush to the
		bottom edge — only its inside is restyled onto generation-panel-concept.html.
		It occupies no flow row, so it needs no wrapper here; the panes above
		reserve their own clearance via `--dock-height`. -->
	{#if !$isMobile}
		{#key currentTab.id}
		<GenerationPanel
			bind:this={generationPanelRef}
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
			multiBackend={currentTabHasMultipleBackends}
			on:generationcomplete={() => lastGenerationsRefreshSignal++}
		>
			<GenerationSettingsPanel
				slot="settings"
				tabId={currentTab.id}
				presetId={currentTab.selectedPreset ?? undefined}
				presetEngine={currentTabPresetEngine}
				mode={currentTab.selectedMode ?? undefined}
				bind:autoTagIds={currentTab.autoTagIds}
				bind:autoCollectionIds={currentTab.autoCollectionIds}
				soundOnComplete={currentTab.soundOnComplete}
				soundOnError={currentTab.soundOnError}
				selectedBackendId={currentTab.selectedBackendId ?? null}
				onBackendEligibilityChange={(visible) => (currentTabHasMultipleBackends = visible)}
			/>
			<LastGenerationsDrawer
				slot="lastGenerations"
				presetId={currentTab.selectedPreset}
				presetName={presets.find((p: any) => p.id === currentTab.selectedPreset)?.name}
				refreshSignal={lastGenerationsRefreshSignal}
				on:reuse={(e) => handleReuseInActiveTab(e.detail)}
			/>
		</GenerationPanel>
		{/key}
	{/if}

</div>

<style>
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
