<script module lang="ts">
	import type { nodeRunView as RunView } from '$lib/elixirs/runs';

	export interface ElixirStep {
		id: string;
		label: string;
		view: ReturnType<typeof RunView>;
	}
</script>

<script lang="ts">
	import { onDestroy, onMount, tick as nextFrame, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import { Button, LoadErrorState, Spinner } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { toasts, type ToastType } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { elixirsApi, type ElixirResult } from '$lib/services/api/elixirs';
	import { elixirRenamed } from '$lib/elixirs/events';
	import { tabsStore } from '$lib/stores/tabs';
	import { embeddedTabFor } from '$lib/types/tabs';
	import { loadPresets } from '$lib/stores/presetsCatalog';
	import { restoreTabSession } from '$lib/session/restoreTabSession';
	import { requestTabSave } from '$lib/stores/workspaceSaveRequest';
	import { tabSessionDirty } from '$lib/stores/tabSessionDirty';
	import { createStepEditor } from '$lib/elixirs/stepEditor';
	import { stepIndex, stepOptions } from '$lib/elixirs/stepEdit';
	import { publishStepBar } from '$lib/elixirs/stepBar';
	import CardLane from './CardLane.svelte';
	import LaneMenu, { type LaneMenuData, type LaneMenuGroup, type LaneMenuItem } from './LaneMenu.svelte';
	import NodePanel from './NodePanel.svelte';
	import ElixirHeader from './ElixirHeader.svelte';
	import ElixirEmptyState from './ElixirEmptyState.svelte';
	import SessionPicker from './SessionPicker.svelte';
	import { openMediaPicker } from '$lib/elixirs/overlays';
	import { tileKey } from '$lib/elixirs/tiles';
	import { openOutputView, type OutputViewHandle } from '$lib/elixirs/outputView';
	import { prepareNodeRequest } from '$lib/elixirs/requests';
	import { directorIssues } from '$lib/elixirs/director';
	import { FOLLOW_ISSUE_CODES, cardMarker, effectiveSettings, sessionHashes, sessionMode, staleReasonText, uniqueSessionName } from '$lib/elixirs/follow';
	import { brewName, isActive, isCurrentRun, isPolling, newIdempotencyKey, nodeRunView, runLabel } from '$lib/elixirs/runs';
	import { laneTakes, nodeChoice, toggled, viewerSendTo, type Lane } from '$lib/elixirs/choice';
	import { readWidths, viewerStorage, withWidth, writeWidths } from '$lib/elixirs/columns';
	import {
		addStepAfter,
		addWriterFor,
		cardSlots,
		chooseFileSource,
		choosePendingPreset,
		choosePromptSource,
		choiceChips,
		clearFileSource,
		fileEdge,
		insertNeeds,
		insertStartBefore,
		insertStepBefore,
		laneOfChild,
		laneTree,
		moveAmongSiblings,
		pendingNeeds,
		presetChoices,
		promptEdge,
		promptMenu,
		removeStep,
		siblingsOf,
		slotMenu,
		startNeeds,
		startPresetChoices,
		type Pending,
		type Slot,
		type StartChoice,
		type SlotChoice,
		type Unavailable
	} from '$lib/elixirs/lane';
	import {
		SETUP_CODES,
		addNode,
		countLabel,
		indexPresets,
		issuesByTarget,
		mediaNoun,
		mediaTypeOf,
		mergePicked,
		mergeSaved,
		nodeLabel,
		nodePorts,
		pickerKeys,
		pinText,
		promptConnected,
		summarizeSnapshot,
		unpinText,
		updateNode,
		worstSeverity,
		writerTarget,
		writerTargets,
		type PickedItem
	} from '$lib/elixirs/model';
	import type {
		ElixirDefinition,
		ElixirNode,
		FollowedState,
		Invocation,
		NodeKind,
		NodeOverrides,
		OutputEntry,
		PresetEntry,
		Run,
		ValidationIssue,
		ValidationReport
	} from '$lib/elixirs/types';

	interface ElixirRecord {
		id: string;
		name: string;
		revision: number;
		run_mode: string;
		definition: ElixirDefinition;
		results?: Record<string, ({ stale?: boolean } & Record<string, unknown>) | undefined>;
		result_revision?: number;
		[key: string]: unknown;
	}

	interface WriterModel {
		id: string;
		name: string;
		vision?: boolean;
	}

	type Issue = ValidationIssue;

	interface Report extends ValidationReport {
		valid?: boolean;
		writer?: { models?: WriterModel[] };
		estimates?: { nodes?: Record<string, { outputs?: number } | undefined> };
	}

	interface RunSummary {
		id: string;
		status: string;
		scope?: string;
		created_at?: string | null;
	}

	interface WrittenEntry {
		for_key: string;
		text: string;
		text_id: string;
	}

	interface SessionChoice {
		session_id: string;
		version?: number | string | null;
	}

	interface EditsPrompt {
		nodeId: string;
		choice: SessionChoice;
		canKeep: boolean;
		message: string;
		resolve: (next: string | null) => void;
	}

	interface Props {
		id: string;
		tabId?: string | null;
		hostPhone?: boolean;
		ontitle?: (name: string) => void;
		ondirty?: (edits: number) => void;
		onstatus?: (run: Run | null, steps: ElixirStep[]) => void;
		onlost?: () => void;
	}

	let { id, tabId = null, hostPhone = false, ontitle, ondirty, onstatus, onlost }: Props = $props();

	const client = elixirsApi();

	function toast(level: ToastType, message: string) {
		toasts.show(level, message);
	}

	const confirmAction = confirmDialog;

	const WRITER_PROBLEMS = new Set(['no_llm', 'llm_cannot_see']);
	const RUN_MODES = [
		{ id: 'review', label: 'Choose outputs between presets' },
		{ id: 'automatic', label: 'Run all branches' }
	];
	const SHORT_RUN_MODES = [
		{ id: 'review', label: 'Choose outputs' },
		{ id: 'automatic', label: 'Run all branches' }
	];
	let record = $state.raw<ElixirRecord | null>(null);
	let loadError = $state<string | null>(null);
	let definition = $state.raw<ElixirDefinition | null>(null);
	let name = $state('');
	let runMode = $state('review');
	let presets = $state.raw<PresetEntry[]>([]);
	let report = $state.raw<Report | null>(null);
	let saveState = $state('saved');
	let saveError = $state<string | null>(null);
	let pending = $state.raw<Record<string, Partial<Pending>>>({});
	let menu = $state.raw<LaneMenuData | null>(null);
	let flashId = $state<string | null>(null);
	let editedIds = $state.raw<string[]>([]);
	let announcement = $state('');
	let widths = $state.raw(untrack(() => readWidths(viewerStorage(), id)));
	let sessionPicker = $state<{ nodeId: string; purpose: string } | null>(null);
	let importing = $state(false);
	let sessionBusy = $state(false);
	let followed = $state.raw<Record<string, FollowedState>>({});
	let lastLiveRefresh = 0;
	let editsPrompt = $state<EditsPrompt | null>(null);
	let lastSaved: ElixirDefinition | null = null;
	let saveTimer: ReturnType<typeof setTimeout> | undefined;
	let flushing: Promise<void> | null = null;
	let again = false;
	let destroyed = false;
	let narrow = $state(false);
	let phone = $derived(narrow || hostPhone);
	let run = $state.raw<Run | null>(null);
	let runs = $state.raw<RunSummary[]>([]);
	let brewing = $state(false);
	let runBusy = $state(false);
	let pollTimer: ReturnType<typeof setTimeout> | undefined;
	let drafts = $state.raw<Record<string, string[]>>({});
	let activeLanes = $state.raw<Record<string, string>>({});
	let continuing = $state.raw(new Set<string>());
	let settingsFor = $state<string | null>(null);
	let settingsOpener: HTMLElement | null = null;
	let viewer = $state.raw<{ nodeId: string; handle: OutputViewHandle } | null>(null);
	const draftTimers = new Map<string, ReturnType<typeof setTimeout>>();
	const draftSaves = new Map<string, Promise<void>>();
	const varsCache = new Map<string, Record<string, unknown>>();

	let presetIndex = $derived(indexPresets(presets));
	let tree = $derived(definition ? laneTree(definition, pending) : null);
	let laneOrder = $derived<string[]>(tree ? tree.roots.flatMap((root) => [root, ...preorder(root)]) : []);
	let grouped = $derived(issuesByTarget(report));
	let allIssues = $derived<Issue[]>(report?.issues ?? []);
	let errorCount = $derived(allIssues.filter((issue) => issue.severity === 'error' && !SETUP_CODES.has(issue.code)).length);
	let settingsTab = $state('quick');
	let settingsNode = $derived(definition && settingsFor ? definition.nodes.find((node) => node.id === settingsFor) ?? null : null);
	let latestId = $derived(runs[0]?.id ?? null);
	let readOnly = $derived(!!run && !!latestId && run.id !== latestId);
	let latestActive = $derived(isActive(runs[0]) || (!!run && run.id === latestId && isActive(run)));
	let steps = $derived(
		definition && run
			? laneOrder
					.map((nodeId) => definition!.nodes.find((node) => node.id === nodeId))
					.filter((node): node is ElixirNode => !!node && node.kind !== 'media' && !!run!.node_states?.[node.id])
					.map((node) => ({ id: node.id, label: nodeLabel(node, presetIndex), view: nodeRunView(run, node) }))
			: []
	);

	async function fetchLatest() {
		const result = await client.getElixir(id);
		if (!result.ok) return null;
		const data = result.data as ElixirRecord;
		return { revision: data.revision, node: (nodeId: string) => data.definition.nodes.find((node) => node.id === nodeId) ?? null, record: data };
	}

	async function commitStep(nodeId: string, overrides: NodeOverrides) {
		if (!definition || !nodeById(nodeId)) return 'error' as const;
		change(updateNode(definition, nodeId, { overrides }), 0);
		await flush();
		if (saveState === 'saved') return 'saved' as const;
		return saveState === 'conflict' ? ('conflict' as const) : ('error' as const);
	}

	const stepEditor = createStepEditor({
		elixirName: () => name.trim() || 'Untitled elixir',
		options: () => stepOptions(definition, laneOrder, presetIndex),
		node: (nodeId) => nodeById(nodeId),
		followed: (nodeId) => followed[nodeId],
		revision: () => record?.revision ?? 0,
		settle: () => settle(),
		fetchLatest,
		adopt: (data) => {
			adopt(data as ElixirRecord);
			void refreshFollowed();
			void validate();
		},
		commit: commitStep,
		tabs: {
			open: (data) => tabsStore.openEmbeddedTab(tabId ?? '', data),
			close: () => tabsStore.closeEmbeddedTab(tabId ?? ''),
			current: () => embeddedTabFor(get(tabsStore).tabs, tabId),
			setBaseline: (baseline) => {
				const tab = embeddedTabFor(get(tabsStore).tabs, tabId);
				if (tab) tabsStore.updateTab(tab.id, { embedded: { ...tab.embedded, baseline } });
			},
			subscribe: (run) => tabsStore.subscribe((state) => run(embeddedTabFor(state.tabs, tabId)))
		},
		host: { setTitle: (title) => ontitle?.(title), setDirty: (edits) => ondirty?.(edits) },
		loadSession: async (tab) => {
			const catalog = await loadPresets();
			await restoreTabSession(tab, { presets: catalog.success && catalog.data ? catalog.data : [] });
		},
		saveSession: (sessionTabId) => requestTabSave(sessionTabId),
		watchSessionDirty: (sessionTabId, run) => tabSessionDirty.subscribe((marks) => run(!!marks[sessionTabId])),
		notify: {
			error: (message) => toast('error', message),
			saved: (message, undo) => toasts.show('success', message, { action: { label: 'Undo', onClick: undo }, duration: 8000 })
		},
		onReturn: (nodeId, saved) => {
			if (saved) editedIds = [...editedIds.filter((item) => item !== nodeId), nodeId];
			void nextFrame().then(() => flash(nodeId));
		},
		now: () => Date.now()
	});

	const stepState = stepEditor.state;

	function openInPreset(nodeId: string) {
		if (!tabId) return;
		void stepEditor.open(nodeId);
	}

	function llmOf(node: ElixirNode): string | null {
		return (node as ElixirNode & { llm_id?: string | null }).llm_id ?? null;
	}

	function preorder(nodeId: string): string[] {
		return (tree!.children.get(nodeId) ?? []).flatMap((child) => [child, ...preorder(child)]);
	}

	function labelOf(nodeId: string): string {
		const node = definition?.nodes.find((item) => item.id === nodeId);
		return node ? nodeLabel(node, presetIndex) : nodeId;
	}

	function sendToFor(nodeId: string) {
		const node = definition?.nodes.find((item) => item.id === nodeId);
		return viewerSendTo(node ? choiceOf(node) : null, (rowId, entry) => {
			const lane = laneFor(nodeId, rowId);
			const key = tileKey(entry);
			if (lane && key && laneTakes(lane, key)) setDraft(lane, toggled(lane.selected, key, lane.eligible));
		});
	}

	function openEntry(nodeId: string, entries: OutputEntry[], index: number) {
		viewer?.handle.close();
		const opened = openOutputView({
			outputs: entries,
			index,
			sendTo: sendToFor(nodeId),
			onclose: () => {
				if (viewer?.handle === opened) viewer = null;
			}
		});
		viewer = opened ? { nodeId, handle: opened } : null;
	}

	$effect(() => {
		const current = viewer;
		if (!current) return;
		const next = { sendTo: sendToFor(current.nodeId), drafts };
		untrack(() => current.handle.update({ sendTo: next.sendTo }));
	});

	function openSettings(nodeId: string) {
		settingsOpener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
		if (settingsFor !== nodeId) settingsTab = 'quick';
		settingsFor = nodeId;
		editsPrompt = null;
		setTimeout(() => revealCard(nodeId), 0);
	}

	function openFromBody(nodeId: string) {
		if (settingsFor === nodeId) return;
		openSettings(nodeId);
	}

	function revealCard(nodeId: string, focus = false) {
		const card = document.querySelector<HTMLElement>(`[data-card-id="${CSS.escape(nodeId)}"]`);
		if (!card) return;
		card.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
		if (focus) card.focus({ preventScroll: true });
	}

	function closeSettings() {
		settingsFor = null;
		settingsTab = 'quick';
		editsPrompt = null;
		const opener = settingsOpener;
		settingsOpener = null;
		if (opener && document.contains(opener)) opener.focus();
	}

	function kindOf(nodeId: string) {
		return definition?.nodes.find((node) => node.id === nodeId)?.kind ?? null;
	}

	function choiceOf(node: ElixirNode) {
		const raw = nodeChoice(run, node.id, { drafts, active: activeLanes[node.id] ?? null, labelFor: labelOf, readOnly });
		return choiceChips(raw, kindOf, tree?.children.get(node.id) ?? []);
	}

	function setLane(nodeId: string, edgeId: string) {
		activeLanes = { ...activeLanes, [nodeId]: edgeId };
	}

	function laneFor(nodeId: string, edgeId: string | null = null) {
		const node = definition!.nodes.find((item) => item.id === nodeId);
		const choice = node ? choiceOf(node) : null;
		const lane = choice?.lanes.find((item) => item.edgeId === (edgeId ?? choice.active));
		return lane && lane.open ? lane : null;
	}

	function setDraft(lane: Lane, keys: string[]) {
		drafts = { ...drafts, ...Object.fromEntries(lane.linked.map((edgeId) => [edgeId, keys])), [lane.edgeId]: keys };
		clearTimeout(draftTimers.get(lane.edgeId));
		const runId = run?.id;
		draftTimers.set(
			lane.edgeId,
			setTimeout(() => {
				draftTimers.delete(lane.edgeId);
				const previous = draftSaves.get(lane.edgeId) ?? Promise.resolve();
				const saving = previous.then(() => saveDraft(runId, lane.edgeId));
				draftSaves.set(lane.edgeId, saving);
				saving.finally(() => {
					if (draftSaves.get(lane.edgeId) === saving) draftSaves.delete(lane.edgeId);
				});
			}, 400)
		);
	}

	async function saveDraft(runId: string | undefined, edgeId: string) {
		if (destroyed || readOnly || !isCurrentRun(run, runId) || !(edgeId in drafts)) return;
		const checkpoint = run?.checkpoints?.[edgeId];
		if (!runId || !checkpoint || checkpoint.confirmed !== null) return;
		const result = await client.saveSelection(runId, edgeId, { expected_revision: checkpoint.revision, selected: drafts[edgeId] });
		if (destroyed || !isCurrentRun(run, runId)) return;
		if (result.ok) {
			setRun(result.data);
			return;
		}
		toast('error', `${result.error.message} The latest choice was loaded again.`);
		const { [edgeId]: _, ...rest } = drafts;
		drafts = rest;
		const fresh = await client.getRun(runId);
		if (fresh.ok && !destroyed && isCurrentRun(run, runId)) setRun(fresh.data);
	}

	function tick(nodeId: string, key: string) {
		const lane = laneFor(nodeId);
		if (lane) setDraft(lane, toggled(lane.selected, key, lane.eligible));
	}

	function selectAll(nodeId: string) {
		const lane = laneFor(nodeId);
		if (lane) setDraft(lane, [...lane.eligible]);
	}

	function clearChoice(nodeId: string) {
		const lane = laneFor(nodeId);
		if (lane) setDraft(lane, []);
	}

	async function continueLane(nodeId: string, edgeId: string) {
		const lane = laneFor(nodeId, edgeId);
		if (!lane || lane.selected.length === 0 || continuing.has(edgeId)) return;
		clearTimeout(draftTimers.get(edgeId));
		draftTimers.delete(edgeId);
		continuing = new Set([...continuing, edgeId]);
		const runId = run!.id as string;
		await draftSaves.get(edgeId);
		const result = await client.continueEdge(runId, edgeId, { expected_revision: run?.checkpoints?.[edgeId]?.revision ?? lane.revision, selected: lane.selected });
		continuing = new Set([...continuing].filter((item) => item !== edgeId));
		if (destroyed || !isCurrentRun(run, runId)) return;
		if (!result.ok) {
			toast('error', result.error.message);
			const fresh = await client.getRun(runId);
			if (fresh.ok && !destroyed && isCurrentRun(run, runId)) setRun(fresh.data);
			return;
		}
		const { [edgeId]: _, ...rest } = drafts;
		drafts = rest;
		const { [nodeId]: __, ...lanes } = activeLanes;
		activeLanes = lanes;
		setRun(result.data);
	}

	function writtenFor(node: ElixirNode) {
		const names: Record<string, string> = {};
		for (const item of (run?.invocations ?? []) as Array<Invocation & { input_ref?: { original_filename?: string } | null }>) {
			if (item.node_id !== node.id || !item.input_ref) continue;
			const name = item.input_ref.original_filename;
			if (name) names[item.input_key] = name;
		}
		return names;
	}

	function pinWritten(nodeId: string, entry: WrittenEntry) {
		change(pinText(definition!, nodeId, entry.for_key, entry.text), 0);
	}

	function unpinWritten(nodeId: string, entry: WrittenEntry) {
		change(unpinText(definition!, nodeId, entry.for_key), 0);
	}

	async function editWritten(nodeId: string, entry: WrittenEntry, text: string) {
		change(pinText(definition!, nodeId, entry.for_key, text), 0);
		if (!run || readOnly || !isActive(run)) {
			toast('success', 'Pinned. The next brew uses your text.');
			return;
		}
		const runId = run.id as string;
		const result = await client.editText(runId, entry.text_id, { text });
		if (destroyed || !isCurrentRun(run, runId)) return;
		if (result.ok) {
			setRun(result.data);
			return;
		}
		if (result.error.code === 'text_sent' || result.error.code === 'run_closed') {
			toast('success', 'Pinned. This prompt was already sent on, so the next brew uses your text.');
			return;
		}
		toast('error', result.error.message);
	}

	function writerSettings(node: ElixirNode) {
		if (node.kind !== 'llm') return null;
		const targetId = writerTarget(definition!, node);
		const problem = (grouped.nodes.get(node.id) ?? []).find((issue) => WRITER_PROBLEMS.has(issue.code));
		return {
			targets: writerTargets(definition!, presetIndex),
			targetLabel: targetId ? labelOf(targetId) : null,
			problem: problem?.message ?? null,
			models: report?.writer?.models ?? []
		};
	}

	function modelLabel(node: ElixirNode) {
		const models = report?.writer?.models ?? [];
		const llmId = llmOf(node);
		if (llmId) {
			const model = models.find((item) => item.id === llmId);
			return model ? model.name : 'Not available any more';
		}
		return models.length > 0 ? `Default (${models[0].name})` : 'Default';
	}

	function slotsOf(node: ElixirNode) {
		const summary = summaryOf(node);
		return cardSlots(definition!, node.id, presetIndex).map((slot) => {
			if (slot.kind === 'prompt') {
				if (slot.source) return { ...slot, writer: true, valueLabel: slot.sourceLabel };
				return { ...slot, empty: !summary?.prompt, valueLabel: summary?.prompt || 'From the session' };
			}
			if (slot.source) return { ...slot, valueLabel: slot.sourceLabel };
			return { ...slot, empty: true, valueLabel: node.kind === 'llm' ? 'Nothing: writes once' : 'From the session' };
		});
	}

	function summaryOf(node: ElixirNode) {
		if (node.kind !== 'preset') return null;
		const mode = sessionMode(node);
		if (mode === 'follow') return summarizeSnapshot(effectiveSettings(node, followed[node.id]), {});
		return summarizeSnapshot(mode === 'copy' ? node.session_snapshot : {}, node.overrides);
	}

	function viaOf(node: ElixirNode) {
		const files = fileEdge(definition!, node.id);
		const text = promptEdge(definition!, node.id);
		if (!files || !text) return null;
		const read = fileEdge(definition!, text.source);
		if (!read || read.source !== files.source || read.source_handle !== files.source_handle) return null;
		return { files: labelOf(files.source), text: labelOf(text.source) };
	}

	function chooserOf(node: ElixirNode) {
		if (node.preset_id) return null;
		const needs = pendingNeeds(definition!, pending[node.id] ?? null, presetIndex);
		const options = presetChoices(presets, needs).map((preset) => ({ ...preset, makes: mediaNoun(Object.values(preset.outputs ?? {})[0]?.media_type) }));
		const title = needs.takes
			? `Choose a preset for the ${mediaNoun(needs.takes)} coming in`
			: needs.prompt
				? 'Choose a preset for the written prompts'
				: 'Choose a preset to start with';
		return { title, options };
	}

	function waitingOf(node: ElixirNode) {
		const parentId = tree?.parent.get(node.id);
		const parent = parentId ? definition!.nodes.find((item) => item.id === parentId) : null;
		if (!parent) return null;
		const lane = laneOfChild(choiceOf(parent), node.id);
		return lane?.open ? { lane, parentId } : null;
	}

	function nodeData(node: ElixirNode): any {
		const issues = grouped.nodes.get(node.id) ?? [];
		const parentId = tree?.parent.get(node.id) ?? null;
		const view = nodeRunView(run, node, { source: parentId ? labelOf(parentId) : null });
		const waiting = waitingOf(node);
		const base = {
			node,
			label: nodeLabel(node, presetIndex),
			issues,
			severity: worstSeverity(issues),
			run: view,
			readOnly,
			panelOpen: settingsFor === node.id,
			stale: !!record?.results?.[node.id]?.stale,
			staleReason: staleReasonText(record?.results?.[node.id], node.id, labelOf),
			busy: runBusy,
			canRetry: !!view && view.failedCount > 0 && isActive(run) && !readOnly,
			runAgainBlocked,
			choice: choiceOf(node),
			continuing,
			waiting,
			statusLabel: waiting ? 'Waiting for your choice' : null,
			statusTone: waiting ? 'signal' : null,
			slots: slotsOf(node),
			onopen: openEntry,
			onsettings: openSettings,
			onopennode: openFromBody,
			onrunagain: runNodeAgain,
			onretry: retryNode,
			onlane: setLane,
			ontick: tick,
			onselectall: selectAll,
			onclear: clearChoice,
			oncontinue: continueLane,
			onslot: openSlotMenu,
			onmodel: openModelMenu,
			onmore: openMoreMenu,
			onopeninpreset: openInPreset,
			canOpenInPreset: !!tabId && !readOnly,
			edited: editedIds.includes(node.id),
			onchoosepreset: chooseCardPreset
		};
		if (node.kind === 'llm') {
			return {
				...base,
				instructionLine: (node.instruction ?? '').trim(),
				targetMissing: !writerTarget(definition!, node),
				modelLabel: modelLabel(node),
				sources: writtenFor(node),
				onedittext: editWritten,
				onpintext: pinWritten,
				onunpintext: unpinWritten
			};
		}
		if (node.kind === 'media') {
			const noun = node.media_type === 'video' ? 'video' : node.media_type === 'image' ? 'image' : 'file';
			const sources = new Set((node.assets ?? []).map((asset) => asset.kind));
			const sourceLine = sources.size === 0 ? 'No files yet' : sources.size > 1 ? 'From History and the Library' : sources.has('library') ? 'From the Library' : 'From History';
			const unused = (node.assets?.length ?? 0) > 0 && (tree?.children.get(node.id) ?? []).length === 0;
			return { ...base, assets: node.assets ?? [], countLine: countLabel(node.assets?.length ?? 0, noun), sourceLine, unused };
		}
		const preset = node.preset_id && node.mode ? presetIndex.get(`${node.preset_id}::${node.mode}`) : null;
		return {
			...base,
			presetLine: preset ? `${preset.preset_name} · ${preset.mode_label}` : node.preset_id ? 'Preset not available' : 'No preset chosen',
			marker: cardMarker(node, followed[node.id], issues),
			issues: issues.filter((issue) => !FOLLOW_ISSUE_CODES.has(issue.code)),
			chooser: chooserOf(node),
			via: viaOf(node),
			makes: Object.values(nodePorts(node, presetIndex).outputs)[0]?.media_type ?? 'image'
		};
	}

	function laneActive(parentId: string, childId: string) {
		const parent = definition!.nodes.find((node) => node.id === parentId);
		const choice = parent ? choiceOf(parent) : null;
		return !!choice?.choosing && laneOfChild(choice, childId)?.edgeId === choice.active;
	}

	function startable(rootId: string) {
		const needs = definition ? startNeeds(definition, rootId, presetIndex) : null;
		return !!needs;
	}

	function openBeforeMenu(anchor: HTMLElement | null, rootId: string) {
		const needs = startNeeds(definition!, rootId, presetIndex);
		const root = definition!.nodes.find((item) => item.id === rootId);
		if (!needs || !root) return;
		const options = startPresetChoices(presets, needs, root.kind);
		const groups: LaneMenuGroup[] = [];
		if (needs.files) {
			const items: LaneMenuItem[] = options.map((preset) => ({ icon: 'flask', name: `${preset.preset_name} · ${preset.mode_label}`, run: () => addBefore(rootId, { kind: 'preset', presetId: preset.preset_id, mode: preset.mode }) }));
			items.unshift({ icon: 'image', name: 'Files', desc: 'Pictures or clips you pick', run: () => addBefore(rootId, { kind: 'files' }) });
			groups.push({ label: `Makes ${mediaNoun(needs.type)} for ${labelOf(rootId)}`, items });
		}
		if (needs.writer) groups.push({ label: 'Writes the prompt', items: [{ ai: true, name: 'Prompt writer', desc: `Writes the prompt for ${labelOf(rootId)}`, run: () => addBefore(rootId, { kind: 'writer' }) }] });
		openMenu(anchor, `Add a step before ${labelOf(rootId)}`, groups);
	}

	function addBefore(rootId: string, choice: StartChoice) {
		const result = insertStartBefore(definition!, rootId, choice, presetIndex);
		if (!result.id) {
			toast('error', 'That step cannot feed the start.');
			return;
		}
		change(result.definition, 0);
		announce(`Step added before ${labelOf(rootId)}`);
		openSettings(result.id);
		flash(result.id);
	}

	function insertable(childId: string) {
		return !!insertNeeds(definition!, childId, presetIndex);
	}

	function announce(text: string) {
		announcement = '';
		setTimeout(() => (announcement = text), 30);
	}

	function flash(nodeId: string) {
		flashId = null;
		setTimeout(() => {
			flashId = nodeId;
			revealCard(nodeId, true);
			setTimeout(() => {
				if (flashId === nodeId) flashId = null;
			}, 1400);
		}, 30);
	}

	function dropPending(nodeId: string) {
		if (!(nodeId in pending)) return;
		const { [nodeId]: _, ...rest } = pending;
		pending = rest;
	}

	function openMenu(anchor: HTMLElement | null, title: string, groups: LaneMenuGroup[]) {
		menu = { anchor, title, groups };
	}

	function iconFor(kind: string) {
		return kind === 'media' ? 'image' : kind === 'llm' ? 'sparkles' : 'flask';
	}

	function openSlotMenu(anchor: HTMLElement | null, nodeId: string, slot: Slot) {
		if (slot.kind === 'prompt') {
			openPromptMenu(anchor, nodeId);
			return;
		}
		const node = definition!.nodes.find((item) => item.id === nodeId);
		if (!node || !slot.port) return;
		const options = slotMenu(definition!, nodeId, slot.port, presetIndex, { tree: tree ?? undefined, estimates: report?.estimates?.nodes ?? {} });
		const row = (item: SlotChoice | Unavailable): LaneMenuItem => ({
			icon: iconFor(item.kind),
			name: item.label,
			desc: ('reason' in item ? item.reason : null) ?? ('detail' in item ? item.detail : null),
			disabled: 'reason' in item && !!item.reason,
			radio: !('reason' in item && item.reason),
			checked: !('reason' in item && item.reason) && item.checked,
			run: () => !item.checked && setFileSource(nodeId, slot, item)
		});
		const groups: LaneMenuGroup[] = [
			{ label: 'Steps', items: options.steps.map(row) },
			{ label: 'Files', items: options.files.map(row) }
		];
		if (node.kind === 'preset' && slot.source) {
			groups.push({
				items: [{ icon: 'undo', name: 'From the session', desc: `Uses the ${(slot.label ?? '').toLowerCase()} saved with this step`, run: () => clearSource(nodeId, slot) }]
			});
		}
		groups.push({ label: 'Not available', items: options.unavailable.map(row) });
		openMenu(anchor, node.kind === 'llm' ? 'Looks at' : `${slot.label} from`, groups);
	}

	function setFileSource(nodeId: string, slot: Slot, item: SlotChoice | Unavailable) {
		if (!slot.port) return;
		const result = chooseFileSource(definition!, nodeId, slot.port, item.source, item.sourceHandle, presetIndex);
		if (!result.verdict.ok) {
			toast('warning', result.verdict.reason ?? '');
			return;
		}
		change(result.definition, 0);
		const label = labelOf(nodeId);
		announce(result.moved.length > 0 ? `${label} and ${labelOf(result.moved[0])} now take their files from ${item.label}` : `${label} now takes its files from ${item.label}`);
		flash(nodeId);
	}

	function clearSource(nodeId: string, slot: Slot) {
		if (!slot.port) return;
		change(clearFileSource(definition!, nodeId, slot.port), 0);
		announce(`${labelOf(nodeId)} uses the ${(slot.label ?? '').toLowerCase()} saved with it`);
		flash(nodeId);
	}

	function openPromptMenu(anchor: HTMLElement | null, nodeId: string) {
		const options = promptMenu(definition!, nodeId, presetIndex);
		openMenu(anchor, 'Prompt', [
			{ items: [{ icon: 'pencil', name: 'From the session', desc: 'The prompt saved with this step', radio: true, checked: !options.current, run: () => setPrompt(nodeId, null) }] },
			{
				label: 'Prompt writers',
				items: options.writers.map((writer) => ({ ai: true, name: writer.label, desc: 'Writes one prompt per file', radio: true, checked: writer.checked, run: () => setPrompt(nodeId, writer.id) }))
			},
			{ items: [{ ai: true, name: 'Write with AI', desc: 'Adds a prompt writer before this step', run: () => writeWithAi(nodeId) }] }
		]);
	}

	function setPrompt(nodeId: string, writerId: string | null) {
		const result = choosePromptSource(definition!, nodeId, writerId, presetIndex);
		if (!result.verdict.ok) {
			toast('warning', result.verdict.reason ?? '');
			return;
		}
		if (result.definition === definition) return;
		const removedLabel = result.removed ? labelOf(result.removed) : null;
		if (result.removed && settingsFor === result.removed) settingsFor = null;
		change(result.definition, 0);
		const label = labelOf(nodeId);
		announce(removedLabel ? `${removedLabel} removed. ${label} uses the session prompt.` : writerId ? `${label} now uses ${labelOf(writerId)}` : `${label} uses the session prompt`);
		flash(nodeId);
	}

	function writeWithAi(nodeId: string) {
		const result = addWriterFor(definition!, nodeId, presetIndex);
		change(result.definition, 0);
		announce(`Prompt writer added before ${labelOf(nodeId)}`);
		openSettings(result.id);
		flash(result.id);
	}

	function openModelMenu(anchor: HTMLElement | null, nodeId: string) {
		const node = definition!.nodes.find((item) => item.id === nodeId);
		if (!node) return;
		const models = report?.writer?.models ?? [];
		const pick = (llm_id: string | null) => change(updateNode(definition!, nodeId, { llm_id } as Partial<ElixirNode>), 0);
		openMenu(anchor, 'Language model', [
			{
				items: [
					{ name: models.length > 0 ? `Default (${models[0].name})` : 'Default', radio: true, checked: !llmOf(node), run: () => pick(null) },
					...models.map((model) => ({ name: model.vision ? `${model.name} · sees images` : model.name, radio: true, checked: llmOf(node) === model.id, run: () => pick(model.id) }))
				]
			}
		]);
	}

	function openMoreMenu(anchor: HTMLElement | null, nodeId: string) {
		const node = definition!.nodes.find((item) => item.id === nodeId);
		if (!node) return;
		const siblings = siblingsOf(tree!, nodeId);
		const at = siblings.indexOf(nodeId);
		const items: LaneMenuItem[] = [];
		if (node.kind === 'preset' && node.preset_id && node.mode && tabId) {
			items.push({ icon: 'external-link', name: 'Open in preset', desc: 'The full Generate view for this step', run: () => openInPreset(nodeId) });
		}
		if (node.kind !== 'media') {
			items.push({ icon: 'refresh', name: 'Run this step again', desc: runAgainBlocked ?? 'Steps after it will need a rerun', disabled: !!runAgainBlocked, run: () => runNodeAgain(nodeId) });
		}
		if (startable(nodeId)) items.push({ icon: 'plus', name: 'Add a step before', desc: `Moves ${labelOf(nodeId)} one column right`, run: () => openBeforeMenu(anchor, nodeId) });
		const needs = insertNeeds(definition!, nodeId, presetIndex);
		if (needs) items.push({ icon: 'plus', name: 'Insert a step before', desc: `Between ${labelOf(needs.parent)} and ${labelOf(nodeId)}`, run: () => openInsertMenu(anchor, nodeId) });
		items.push({ icon: 'chevron-up', name: 'Move up', desc: 'Among steps that take the same input', disabled: at <= 0, run: () => move(nodeId, -1) });
		items.push({ icon: 'chevron-down', name: 'Move down', disabled: at === -1 || at >= siblings.length - 1, run: () => move(nodeId, 1) });
		openMenu(anchor, labelOf(nodeId), [{ items }, { items: [{ icon: 'trash', name: 'Remove step', danger: true, run: () => confirmRemove(nodeId) }] }]);
	}

	function move(nodeId: string, direction: number) {
		const next = moveAmongSiblings(definition!, tree!, nodeId, direction);
		if (next === definition) return;
		change(next, 0);
		announce(`${labelOf(nodeId)} moved ${direction < 0 ? 'up' : 'down'}`);
		flash(nodeId);
	}

	function openInsertMenu(anchor: HTMLElement | null, childId: string) {
		const needs = insertNeeds(definition!, childId, presetIndex);
		if (!needs) return;
		const options = presetChoices(presets, { takes: needs.takes, makes: needs.makes });
		openMenu(anchor, `Insert before ${labelOf(childId)}`, [
			{
				label: `Takes ${mediaNoun(needs.takes)} from ${labelOf(needs.parent)}, makes ${mediaNoun(needs.makes)}`,
				items:
					options.length > 0
						? options.map((preset) => ({ icon: 'flask', name: `${preset.preset_name} · ${preset.mode_label}`, run: () => insertBefore(childId, preset) }))
						: [{ name: 'No installed preset fits here', disabled: true }]
			}
		]);
	}

	function insertBefore(childId: string, preset: PresetEntry) {
		const result = insertStepBefore(definition!, childId, preset.preset_id, preset.mode, presetIndex);
		if (!result.id) return;
		change(result.definition, 0);
		announce(`${preset.preset_name} · ${preset.mode_label} added before ${labelOf(childId)}`);
		openSettings(result.id);
		flash(result.id);
	}

	function openNextMenu(anchor: HTMLElement | null, nodeId: string) {
		const node = definition!.nodes.find((item) => item.id === nodeId);
		if (!node) return;
		const leaf = (tree!.children.get(nodeId) ?? []).length === 0;
		const label = labelOf(nodeId);
		const type = Object.values(nodePorts(node, presetIndex).outputs)[0]?.media_type ?? null;
		const items: LaneMenuItem[] = [
			{
				icon: 'flask',
				name: 'Preset step',
				desc: node.kind === 'llm' ? `Uses the prompts from ${label}` : type ? `Takes the ${mediaNoun(type)} from ${label}` : `Comes after ${label}`,
				run: () => addAfter(nodeId, false)
			}
		];
		if (node.kind !== 'llm' && type) items.push({ ai: true, name: 'Preset step with a prompt writer', desc: `Adds a writer that reads ${label} first`, run: () => addAfter(nodeId, true) });
		openMenu(anchor, leaf ? `Next step after ${label}` : `Another branch after ${label}`, [{ items }]);
	}

	function addAfter(nodeId: string, withWriter: boolean) {
		const result = addStepAfter(definition!, nodeId, presetIndex, { withWriter });
		if (result.pending) pending = { ...pending, [result.id]: result.pending };
		change(result.definition, 0);
		announce(withWriter ? 'Prompt writer and step added. Choose a preset for it.' : 'Step added. Choose a preset for it.');
		flash(result.id);
	}

	function openStartMenu(anchor: HTMLElement | null) {
		openMenu(anchor, 'Add a start', [
			{
				items: [
					{ icon: 'flask', name: 'Preset step', desc: 'Make images or video from a prompt', run: () => addNodeOf('preset', false) },
					{ icon: 'image', name: 'Files', desc: 'Use images or videos from History or the Library', run: () => addNodeOf('media') }
				]
			}
		]);
	}

	function chooseCardPreset(nodeId: string, presetId: string, mode: string) {
		change(choosePendingPreset(definition!, nodeId, presetId, mode, pending[nodeId] ?? null, presetIndex), 0);
		dropPending(nodeId);
		announce(`${labelOf(nodeId)} added`);
		openSettings(nodeId);
	}

	async function confirmRemove(nodeId: string) {
		const node = definition!.nodes.find((item) => item.id === nodeId);
		if (!node) return;
		const label = labelOf(nodeId);
		const kids = tree!.children.get(nodeId) ?? [];
		const files = fileEdge(definition!, nodeId);
		const message =
			kids.length === 0
				? 'Its outputs from past brews stay in History.'
				: node.kind === 'llm'
					? `${countLabel(kids.length, 'step')} after it will use the prompt saved in their session instead.`
					: files
						? `${countLabel(kids.length, 'step')} after it will take their input from ${labelOf(files.source)} instead, where it fits.`
						: 'Steps after it will have no input and need a new source.';
		const confirmed = await confirmAction({ title: `Remove ${label}?`, message, variant: 'danger', confirmLabel: 'Remove step' });
		if (!confirmed || destroyed || !definition || !definition.nodes.some((item) => item.id === nodeId)) return;
		const result = removeStep(definition, nodeId, presetIndex);
		if (settingsFor === nodeId) {
			settingsFor = null;
			settingsOpener = null;
		}
		dropPending(nodeId);
		change(result.definition, 0);
		announce(`${label} removed`);
	}

	function setWidth(index: number, width: number) {
		widths = withWidth(widths, index, width);
	}

	function saveWidths() {
		writeWidths(viewerStorage(), id, widths);
	}

	async function load() {
		loadError = null;
		const [elixir, presetList] = await Promise.all([client.getElixir(id), client.listPresets()]);
		if (!elixir.ok) {
			loadError = elixir.error.code === 'elixir_not_found' ? 'This elixir does not exist or is not yours.' : elixir.error.message;
			return;
		}
		presets = presetList.ok ? presetList.data.presets : [];
		if (!presetList.ok) toast('warning', presetList.error.message);
		adopt(elixir.data);
		await refreshFollowed();
		await validate();
		await loadRuns();
		if (!destroyed && tabId) await stepEditor.resume();
	}

	async function loadRuns() {
		const listed = await client.listRuns(id);
		if (!listed.ok || destroyed) return;
		runs = listed.data.runs;
		if (runs.length > 0) await showRun(runs[0].id);
	}

	async function showRun(runId: string) {
		const result = await client.getRun(runId);
		if (result.ok && !destroyed) setRun(result.data);
	}

	function setRun(next: Run) {
		const finished = run && run.id === next.id && isActive(run) && !isActive(next);
		if (run?.id !== next.id) {
			for (const timer of draftTimers.values()) clearTimeout(timer);
			draftTimers.clear();
			drafts = {};
			activeLanes = {};
		}
		run = next;
		const summary: RunSummary = { id: next.id as string, status: next.status, scope: next.scope, created_at: next.created_at };
		runs = runs.some((item) => item.id === next.id)
			? runs.map((item) => (item.id === next.id ? { ...item, ...summary } : item))
			: [summary, ...runs];
		schedulePoll();
		if (finished) refreshResults();
	}

	function schedulePoll() {
		clearTimeout(pollTimer);
		if (destroyed || !isPolling(run)) return;
		const runId = run!.id as string;
		pollTimer = setTimeout(async () => {
			const result = await client.getRun(runId);
			if (destroyed || run?.id !== runId) return;
			if (result.ok) setRun(result.data);
			else schedulePoll();
		}, 2000);
	}

	async function refreshResults() {
		const result = await client.getElixir(id);
		if (!result.ok || destroyed || !record) return;
		const moved = result.data.result_revision !== record.result_revision;
		record = { ...record, results: result.data.results, result_revision: result.data.result_revision };
		if (moved) await refreshFollowed();
	}

	async function refreshFollowed() {
		const result = await client.followedSessions(id);
		if (!result.ok || destroyed) return false;
		followed = result.data.nodes ?? {};
		return true;
	}

	async function refreshLive() {
		if (destroyed || !record || document.visibilityState === 'hidden' || Date.now() - lastLiveRefresh < 2000) return;
		lastLiveRefresh = Date.now();
		const [fresh] = await Promise.all([client.getElixir(id), refreshFollowed()]);
		if (!fresh.ok || destroyed || !record) return;
		record = { ...record, results: fresh.data.results, result_revision: fresh.data.result_revision };
		if (saveState !== 'saved' || fresh.data.revision !== record.revision) return;
		definition = mergeSaved(definition!, definition!, fresh.data.definition);
		lastSaved = fresh.data.definition;
		validate();
	}

	async function presetVarsFor(presetId: string | null | undefined) {
		if (!presetId) return null;
		if (varsCache.has(presetId)) return varsCache.get(presetId) ?? null;
		const result = await client.presetVars(presetId);
		if (!result.ok) return null;
		varsCache.set(presetId, result.data);
		return result.data;
	}

	async function prepareTemplate(node: ElixirNode): Promise<{ error?: string; request?: Record<string, unknown> }> {
		const vars = await presetVarsFor(node.preset_id);
		const label = nodeLabel(node, presetIndex);
		if (!vars) return { error: `${label}: its preset could not be loaded.` };
		const prepared = prepareNodeRequest(node, vars, undefined, { promptConnected: promptConnected(definition!, node.id), followed: followed[node.id] });
		if (!prepared) return { error: `${label}: it needs a preset and settings first.` };
		if (!prepared.ok) return { error: `${label}: ${prepared.message}` };
		return { request: prepared.request as unknown as Record<string, unknown> };
	}

	async function sessionChanged() {
		await Promise.all([refreshFollowed(), refreshResults()]);
		toast('warning', 'A followed session changed. Brew again.');
	}

	async function brew() {
		if (!record || brewing) return;
		brewing = true;
		try {
			if (!(await settle())) {
				toast('error', 'Save the elixir before brewing.');
				return;
			}
			let result: ElixirResult | null = null;
			for (let attempt = 0; attempt < 2; attempt += 1) {
				await refreshFollowed();
				const templates: Record<string, unknown> = {};
				for (const node of definition!.nodes.filter((item) => item.kind === 'preset')) {
					const prepared = await prepareTemplate(node);
					if (prepared.error) {
						toast('error', prepared.error);
						return;
					}
					templates[node.id] = prepared.request;
				}
				result = await client.startBrew(id, {
					expected_revision: record!.revision,
					idempotency_key: newIdempotencyKey('brew'),
					templates,
					session_hashes: sessionHashes(definition!, followed),
					run_mode: runMode
				});
				if (result.ok || result.error.code !== 'session_changed') break;
			}
			if (!result) return;
			if (!result.ok && result.error.code === 'session_changed') {
				await sessionChanged();
				return;
			}
			if (!result.ok) {
				if (Array.isArray(result.error.issues)) report = { ...(report || {}), valid: false, issues: result.error.issues as Issue[] };
				toast('error', result.error.message);
				return;
			}
			setRun(result.data);
		} finally {
			brewing = false;
		}
	}

	async function stopBrew() {
		if (!latestId) return;
		runBusy = true;
		const result = await client.cancelRun(latestId);
		runBusy = false;
		if (!result.ok) toast('error', result.error.message);
		else setRun(result.data);
	}

	async function retryNode(nodeId: string) {
		if (!run || readOnly) return;
		runBusy = true;
		const result = await client.retryFailed(run.id as string, nodeId);
		runBusy = false;
		if (!result.ok) toast('error', result.error.message);
		else setRun(result.data);
	}

	async function runNodeAgain(nodeId: string) {
		const node = definition?.nodes.find((item) => item.id === nodeId);
		if (!node || runBusy) return;
		runBusy = true;
		try {
			if (!(await settle())) {
				toast('error', 'Save the elixir before running a node.');
				return;
			}
			await refreshFollowed();
			let template: Record<string, unknown> = {};
			if (node.kind === 'llm') {
				const targetId = writerTarget(definition!, node);
				const target = targetId ? definition!.nodes.find((item) => item.id === targetId) : null;
				const prepared = target ? await prepareTemplate(target) : null;
				if (prepared?.request) template = prepared.request;
			} else {
				const prepared = await prepareTemplate(node);
				if (prepared.error) {
					toast('error', prepared.error);
					return;
				}
				template = prepared.request ?? {};
			}
			const result = await client.runNode(id, nodeId, {
				expected_revision: record!.revision,
				idempotency_key: newIdempotencyKey('node'),
				template,
				session_hashes: sessionHashes(definition!, followed)
			});
			if (!result.ok && result.error.code === 'session_changed') await sessionChanged();
			else if (!result.ok) toast('error', result.error.message);
			else setRun(result.data);
		} finally {
			runBusy = false;
		}
	}

	let brewBlocked = $derived(
		!definition
			? 'Loading'
			: definition.nodes.length === 0
				? 'Add a node first.'
				: saveState === 'conflict' || saveState === 'error'
					? 'Save the elixir first.'
					: report && !report.valid
						? 'Fix the problems first.'
						: null
	);
	let runAgainBlocked = $derived(isPolling(run) ? 'Wait for the current brew to finish, or stop it.' : null);

	function adopt(data: ElixirRecord) {
		record = data;
		lastSaved = data.definition;
		definition = data.definition;
		name = data.name;
		runMode = data.run_mode;
		saveState = 'saved';
		saveError = null;
		editsPrompt = null;
	}

	async function validate() {
		if (!record) return;
		const revision = record.revision;
		const result = await client.validateElixir(id);
		if (destroyed || !result.ok || !record || record.revision !== revision) return;
		const extra = definition ? await directorIssues(definition, presetIndex, prepareTemplate) : [];
		if (destroyed || !record || record.revision !== revision) return;
		const issues = [...(result.data.issues ?? []), ...extra];
		report = { ...result.data, issues, valid: result.data.valid && !extra.length };
	}

	function change(next: ElixirDefinition, delay = 500) {
		definition = next;
		scheduleSave(delay);
	}

	function scheduleSave(delay = 500) {
		if (saveState === 'conflict') return;
		saveState = 'dirty';
		clearTimeout(saveTimer);
		saveTimer = setTimeout(() => flush(), delay);
	}

	function revertRejected(nodeId: string) {
		if (!definition) return;
		const saved = lastSaved?.nodes.find((node) => node.id === nodeId);
		const current = definition.nodes.find((node) => node.id === nodeId);
		if (!current || current.kind !== 'media') return;
		definition = saved
			? updateNode(definition, nodeId, { assets: saved.assets, media_type: saved.media_type })
			: updateNode(definition, nodeId, { assets: [], media_type: null });
	}

	function flush() {
		clearTimeout(saveTimer);
		if (flushing) {
			again = true;
			return flushing;
		}
		flushing = runSave().finally(() => {
			flushing = null;
		});
		return flushing;
	}

	async function runSave() {
		for (;;) {
			clearTimeout(saveTimer);
			if (saveState === 'conflict' || !record || destroyed) return;
			again = false;
			const sent = definition!;
			saveState = 'saving';
			const result = await client.updateElixir(id, {
				expected_revision: record.revision,
				name: name.trim() || 'Untitled elixir',
				run_mode: runMode,
				definition: sent
			});
			if (destroyed) return;
			if (!result.ok) {
				if (result.error.code === 'revision_conflict') {
					saveState = 'conflict';
					return;
				}
				const rejected = result.error.node_id ? definition!.nodes.find((node) => node.id === result.error.node_id) : null;
				if (rejected?.kind === 'media') {
					revertRejected(rejected.id);
					toast('error', result.error.message);
					continue;
				}
				saveState = 'error';
				saveError = result.error.message;
				return;
			}
			const changed = again || definition !== sent;
			record = result.data;
			lastSaved = result.data.definition;
			definition = mergeSaved(definition!, sent, result.data.definition);
			saveError = null;
			if (changed) continue;
			saveState = 'saved';
			validate();
			return;
		}
	}

	async function settle() {
		clearTimeout(saveTimer);
		if (saveState === 'dirty' || saveState === 'error' || flushing) await flush();
		return saveState === 'saved';
	}

	async function reload() {
		clearTimeout(saveTimer);
		record = null;
		definition = null;
		report = null;
		pending = {};
		await load();
	}

	function addNodeOf(kind: string, open = true) {
		if (!definition) return;
		const result = addNode(definition, kind as NodeKind);
		change(result.definition, 0);
		if (open) {
			settingsOpener = null;
			settingsTab = 'quick';
			settingsFor = result.id;
		}
		flash(result.id);
	}

	function focusNode(nodeId: string | null | undefined) {
		if (!nodeId) return;
		flash(nodeId);
	}

	function focusIssue(issue: Issue) {
		focusNode(issue.node_id ?? (issue.edge_id ? definition?.edges.find((edge) => edge.id === issue.edge_id)?.target : null));
	}

	function removeSettingsNode() {
		if (settingsFor) confirmRemove(settingsFor);
	}

	function renameNode(title: string | null) {
		if (!settingsFor) return;
		change(updateNode(definition!, settingsFor, { title }), 0);
	}

	function changeWriter(changes: Partial<ElixirNode>) {
		if (!settingsFor) return;
		change(updateNode(definition!, settingsFor, changes), 'instruction' in changes ? 600 : 0);
	}

	function changeSettings(overrides: NodeOverrides) {
		if (!settingsFor) return;
		change(updateNode(definition!, settingsFor, { overrides }), 800);
	}

	function pickPreset(presetId: string, mode: string) {
		const nodeId = settingsFor;
		if (!nodeId) return;
		change(choosePendingPreset(definition!, nodeId, presetId, mode, pending[nodeId] ?? null, presetIndex), 0);
		dropPending(nodeId);
	}

	function pickMedia(kind: 'history' | 'library') {
		const node = settingsNode;
		if (!node) return;
		const nodeId = node.id;
		openMediaPicker(kind, {
			selectedKeys: pickerKeys(node.assets, kind).filter((key): key is string => !!key),
			mediaTypes: node.media_type ? [node.media_type] : ['image', 'video'],
			onConfirm: (_keys: unknown, items: PickedItem[]) => {
				const current = definition?.nodes.find((item) => item.id === nodeId);
				if (!current) return;
				const assets = mergePicked(current.assets, kind, items);
				const media_type = current.media_type ?? mediaTypeOf(assets);
				if (!current.media_type && assets.length > 0 && !media_type) {
					toast('warning', 'Pick only images or only videos for one media source.');
					return;
				}
				change(updateNode(definition!, nodeId, { assets, media_type: assets.length ? media_type : null }), 0);
			}
		});
	}

	function removeAsset(index: number) {
		const node = settingsNode;
		if (!node) return;
		const assets = (node.assets ?? []).filter((_, position) => position !== index);
		change(updateNode(definition!, node.id, { assets, media_type: assets.length ? node.media_type : null }), 0);
	}

	function clearMedia() {
		if (!settingsFor) return;
		change(updateNode(definition!, settingsFor, { assets: [], media_type: null }), 0);
	}

	async function importSession(nodeId: string, choice: SessionChoice, policy: string | null = null) {
		if (!nodeId) return;
		importing = true;
		const saved = await settle();
		if (!saved) {
			importing = false;
			toast('error', 'Save the elixir before copying a session.');
			return;
		}
		const before = definition;
		const result = await client.importSession(id, nodeId, {
			expected_revision: record!.revision,
			session_id: choice.session_id,
			version: choice.version,
			mode: definition?.nodes.find((node) => node.id === nodeId)?.mode ?? null,
			overrides: policy
		});
		importing = false;
		if (destroyed) return;
		sessionPicker = null;
		if (!result.ok) {
			if (result.error.code === 'node_has_local_edits' || result.error.code === 'local_edits_do_not_apply') {
				editsPrompt = {
					nodeId,
					choice,
					canKeep: result.error.code === 'node_has_local_edits',
					message: result.error.message,
					resolve: (next: string | null) => {
						const waiting = editsPrompt;
						editsPrompt = null;
						if (next && waiting) importSession(waiting.nodeId, waiting.choice, next);
					}
				};
				return;
			}
			if (result.error.code === 'revision_conflict') {
				saveState = 'conflict';
				return;
			}
			toast('error', result.error.message);
			return;
		}
		const server = (result.data.definition as ElixirDefinition).nodes.find((node) => node.id === nodeId);
		const editedMeanwhile = definition !== before;
		record = result.data;
		lastSaved = result.data.definition;
		if (server) definition = updateNode(definition!, nodeId, server);
		editsPrompt = null;
		toast('success', `Copied settings from "${server?.session_source?.name ?? 'the session'}"`);
		if (editedMeanwhile) scheduleSave(0);
		else {
			saveState = 'saved';
			validate();
		}
		refreshFollowed();
	}

	function nodeById(nodeId: string) {
		return definition?.nodes.find((node) => node.id === nodeId) ?? null;
	}

	async function changeSession(nodeId: string, send: (revision: number) => Promise<ElixirResult>, success: ((server: ElixirNode | undefined) => string) | null) {
		if (sessionBusy) return false;
		sessionBusy = true;
		try {
			if (!(await settle())) {
				toast('error', 'Save the elixir before changing where its settings come from.');
				return false;
			}
			const before = definition;
			const result = await send(record!.revision);
			if (destroyed) return false;
			if (!result.ok) {
				if (result.error.code === 'revision_conflict') saveState = 'conflict';
				else toast('error', result.error.message);
				return false;
			}
			const server: ElixirNode | undefined = result.data.node ?? (result.data.definition as ElixirDefinition).nodes.find((node) => node.id === nodeId);
			const editedMeanwhile = definition !== before;
			record = result.data;
			lastSaved = result.data.definition;
			if (server) definition = updateNode(definition!, nodeId, server);
			editsPrompt = null;
			await refreshFollowed();
			if (destroyed) return false;
			if (editedMeanwhile) scheduleSave(0);
			else {
				saveState = 'saved';
				validate();
			}
			if (success) toast('success', success(server));
			return true;
		} finally {
			sessionBusy = false;
		}
	}

	async function followSession(nodeId: string, sessionId: string, sessionName: string | null = null) {
		const node = nodeById(nodeId);
		if (!node) return false;
		if (sessionMode(node) !== 'follow' && Object.keys(node.overrides ?? {}).length > 0) {
			const confirmed = await confirmAction({
				title: `Follow ${sessionName ? `"${sessionName}"` : 'the session'}?`,
				message: `${labelOf(nodeId)} drops its own edits and takes its settings from the session from now on.`,
				confirmLabel: 'Follow session'
			});
			if (!confirmed || destroyed) return false;
		}
		const done = await changeSession(
			nodeId,
			(revision) => client.followSession(id, nodeId, { expected_revision: revision, session_id: sessionId, mode: node.mode }),
			(server) => `${labelOf(nodeId)} follows "${server?.session_ref?.name ?? sessionName ?? 'the session'}"`
		);
		if (done) sessionPicker = null;
		return done;
	}

	function keepCopy(nodeId: string) {
		return changeSession(
			nodeId,
			(revision) => client.copySession(id, nodeId, { expected_revision: revision }),
			(server) => `${labelOf(nodeId)} keeps a copy of "${server?.session_source?.name ?? 'the session'}"`
		);
	}

	function ownSettings(nodeId: string) {
		return changeSession(
			nodeId,
			(revision) => client.ownSettings(id, nodeId, { expected_revision: revision }),
			() => `${labelOf(nodeId)} keeps its own settings`
		);
	}

	function updateFromSession(nodeId: string) {
		const node = nodeById(nodeId);
		if (!node?.session_source?.id) return;
		importSession(nodeId, { session_id: node.session_source.id, version: null });
	}

	async function saveAsSession(nodeId: string) {
		const node = nodeById(nodeId);
		if (!node?.preset_id || !node.mode || sessionBusy) return;
		sessionBusy = true;
		let saved: { id: string; name: string } | null = null;
		try {
			const listed = await client.listSessions(node.preset_id);
			if (!listed.ok) {
				toast('error', listed.error.message);
				return;
			}
			const name = uniqueSessionName(labelOf(nodeId), listed.data.map((session: { name: string }) => session.name));
			const settings = effectiveSettings(node, followed[nodeId]) ?? {};
			const result = await client.saveSession({ preset_id: node.preset_id, name, data: { [node.mode]: settings } });
			if (destroyed) return;
			if (!result.ok) {
				toast('error', result.error.message);
				return;
			}
			saved = result.data;
		} finally {
			sessionBusy = false;
		}
		const made = saved as { id: string; name: string } | null;
		if (!made || !nodeById(nodeId)) return;
		await changeSession(
			nodeId,
			(revision) => client.followSession(id, nodeId, { expected_revision: revision, session_id: made.id, mode: nodeById(nodeId)?.mode ?? null }),
			() => `Saved as "${made.name}". ${labelOf(nodeId)} follows it now.`
		);
	}

	function pickSession(nodeId: string, purpose: string) {
		sessionPicker = { nodeId, purpose };
	}

	function setSessionMode(nodeId: string, next: string) {
		const node = nodeById(nodeId);
		const current = node ? sessionMode(node) : null;
		if (!node || !current || current === next || sessionBusy) return;
		if (next === 'new') ownSettings(nodeId);
		else if (next === 'copy') {
			if (current === 'follow') keepCopy(nodeId);
			else pickSession(nodeId, 'copy');
		} else if (current === 'copy' && node.session_source?.id) followSession(nodeId, node.session_source.id, node.session_source.name);
		else pickSession(nodeId, 'follow');
	}

	function onpicked(choice: SessionChoice & { name?: string }) {
		const target = sessionPicker;
		if (!target) return;
		if (target.purpose === 'follow') followSession(target.nodeId, choice.session_id, choice.name ?? null);
		else importSession(target.nodeId, choice);
	}

	function setRunMode(next: string) {
		if (next === runMode) return;
		runMode = next;
		scheduleSave(0);
	}

	function commitName() {
		const next = name.trim();
		if (!next) {
			name = record?.name ?? '';
			return;
		}
		if (next !== record?.name) scheduleSave(0);
	}

	function onwindowkeydown(event: KeyboardEvent) {
		if (event.key !== 'Escape' || event.defaultPrevented || menu) return;
		if (document.querySelector('.elixir-header[data-panel-open]')) return;
		if (!settingsFor || sessionPicker) return;
		if (document.querySelector('[role="dialog"]:not(.drawer)')) return;
		closeSettings();
	}

	function onbeforeunload(event: BeforeUnloadEvent) {
		if (saveState === 'dirty' || saveState === 'saving' || $stepState.edits > 0) event.preventDefault();
	}

	let statusText = $derived(
		(
		{
			saved: 'Saved',
			dirty: 'Unsaved changes',
			saving: 'Saving…',
			error: 'Not saved',
			conflict: 'Changed elsewhere'
		} as Record<string, string>
		)[saveState]
	);

	onMount(() => {
		const query = window.matchMedia('(max-width: 639px)');
		narrow = query.matches;
		const follow = (event: MediaQueryListEvent) => {
			narrow = event.matches;
		};
		query.addEventListener('change', follow);
		load();
		const live = () => refreshLive();
		window.addEventListener('focus', live);
		document.addEventListener('visibilitychange', live);
		return () => {
			query.removeEventListener('change', follow);
			window.removeEventListener('focus', live);
			document.removeEventListener('visibilitychange', live);
		};
	});

	onDestroy(() => {
		destroyed = true;
		stepEditor.destroy();
		if (tabId) publishStepBar(tabId, null);
		viewer?.handle.close();
		for (const timer of draftTimers.values()) clearTimeout(timer);
		clearTimeout(saveTimer);
		clearTimeout(pollTimer);
		if (saveState === 'dirty' && record && definition) {
			client.updateElixir(id, { expected_revision: record.revision, name: name.trim() || 'Untitled elixir', run_mode: runMode, definition });
		}
	});

	$effect(() => {
		const title = record?.name;
		const stepping = !!$stepState.nodeId;
		if (title && !stepping) untrack(() => ontitle?.(title));
	});

	$effect(() => {
		const state = $stepState;
		const options = stepOptions(definition, laneOrder, presetIndex);
		const owner = tabId;
		if (!owner) return;
		if (!state.nodeId) {
			publishStepBar(owner, null);
			return;
		}
		publishStepBar(owner, {
			elixirName: name.trim() || 'Untitled elixir',
			stepLabel: state.label,
			follow: state.follow,
			sessionName: state.sessionName,
			steps: options,
			index: stepIndex(options, state.nodeId),
			edits: state.edits,
			saving: state.saving,
			opening: state.opening,
			confirm: state.confirm,
			conflict: state.conflict,
			back: () => void stepEditor.back(),
			go: (nodeId) => void stepEditor.go(nodeId),
			save: () => void stepEditor.save(),
			answerConfirm: (choice) => void stepEditor.answerConfirm(choice),
			resolveConflict: (choice) => void stepEditor.resolveConflict(choice)
		});
	});

	$effect(() => {
		const current = run;
		const list = steps;
		untrack(() => onstatus?.(current, list));
	});

	$effect(() => {
		const renamed = $elixirRenamed;
		if (!renamed || renamed.id !== id) return;
		untrack(() => {
			if (!record || saveState !== 'saved' || renamed.revision <= record.revision) return;
			record = { ...record, name: renamed.name, revision: renamed.revision, updated_at: renamed.updated_at ?? record.updated_at };
			name = renamed.name;
		});
	});

	function startWith(kind: 'preset' | 'media') {
		if (kind === 'preset') addNodeOf('preset', false);
		else addNodeOf('media');
	}
</script>


<svelte:window {onbeforeunload} onkeydown={onwindowkeydown} />

<div class="elixirs-root flex h-full min-h-0 flex-col" data-testid="elixir-tab" data-elixir-id={id}>
{#if loadError}
	<div class="p-6">
		<LoadErrorState title="This elixir could not be opened" message={loadError} onRetry={load} />
		{#if onlost}
			<div class="mt-4 flex justify-center">
				<Button variant="secondary" size="sm" icon="flask" onclick={onlost}>Open another elixir</Button>
			</div>
		{/if}
	</div>
{:else if !definition}
	<div class="flex flex-1 items-center justify-center py-16" role="status" aria-label="Loading elixir">
		<Spinner size="md" />
	</div>
{:else}
	<div class="flex min-h-0 flex-1 flex-col">
		<ElixirHeader
			bind:name
			{phone}
			dockBrew={phone}
			{runMode}
			runModes={RUN_MODES}
			shortRunModes={SHORT_RUN_MODES}
			{saveState}
			{statusText}
			issues={allIssues}
			{errorCount}
			setupCodes={SETUP_CODES}
			{run}
			{runs}
			{steps}
			{latestActive}
			{runBusy}
			{brewing}
			{brewBlocked}
			stopping={runs[0]?.status === 'cancelling'}
			oncommitname={commitName}
			onrunmode={setRunMode}
			onadd={addNodeOf}
			onbrew={brew}
			onstop={stopBrew}
			onshowrun={showRun}
			onfocusissue={focusIssue}
		/>

		{#if saveState === 'conflict'}
			<div class="flex flex-wrap items-center gap-3 border-b border-line bg-surface-1 px-4 py-2">
				<p class="min-w-0 flex-1 text-sm text-danger" role="alert">
					This elixir was changed in another tab or window. Reload it to continue; changes made here since the last save are lost.
				</p>
				<Button variant="secondary" size="sm" icon="refresh" onclick={reload}>Reload</Button>
			</div>
		{:else if saveState === 'error'}
			<div class="flex flex-wrap items-center gap-3 border-b border-line bg-surface-1 px-4 py-2">
				<p class="min-w-0 flex-1 text-sm text-danger" role="alert">{saveError}</p>
				<Button variant="secondary" size="sm" icon="refresh" onclick={() => flush()}>Try again</Button>
			</div>
		{/if}

		{#if readOnly}
			<div class="flex flex-wrap items-center gap-3 border-b border-line bg-signal/10 px-4 py-2" role="status">
				<p class="min-w-0 flex-1 text-sm text-signal">
					Viewing {brewName(runs, run?.id ?? '')} · {runLabel(run)}. Its outputs stay as they were; choices and retries happen on the latest brew.
				</p>
				<Button variant="secondary" size="sm" icon="arrow-left" onclick={() => latestId && showRun(latestId)}>Back to the latest brew</Button>
			</div>
		{/if}

		<div class="relative flex min-h-0 flex-1">
			{#if definition.nodes.length === 0}
				<ElixirEmptyState onstart={startWith} />
			{:else}
			<CardLane
				{definition}
				tree={tree!}
				{nodeData}
				{phone}
				{readOnly}
				{widths}
				{flashId}
				{labelOf}
				{insertable}
				{startable}
				onbefore={openBeforeMenu}
				{laneActive}
				onwidth={setWidth}
				onwidthdone={saveWidths}
				onnext={openNextMenu}
				oninsert={openInsertMenu}
				onstart={openStartMenu}
			/>
			{/if}

			{#if settingsNode}
				<div
					class="drawer fixed inset-x-0 bottom-16 z-40 max-h-[80dvh] min-h-[45dvh] overflow-hidden rounded-t-xl border-t border-line shadow-overlay sm:static sm:z-auto sm:max-h-none sm:min-h-0 sm:w-[26rem] sm:max-w-[50%] sm:flex-none sm:rounded-none sm:border-l sm:border-t-0 sm:shadow-none"
				>
					{#key settingsNode.id}
						<NodePanel
							node={settingsNode as ElixirNode & { assets: NonNullable<ElixirNode['assets']> }}
							{client}
							label={nodeLabel(settingsNode, presetIndex)}
							{presets}
							editsPrompt={editsPrompt?.nodeId === settingsNode.id ? editsPrompt : null}
							onrename={renameNode}
							onsettingschange={changeSettings}
							onchoosepreset={pickPreset}
							followedState={followed[settingsNode.id]}
							issues={grouped.nodes.get(settingsNode.id) ?? []}
							{sessionBusy}
							onsessionmode={(mode) => setSessionMode(settingsNode.id, mode)}
							onpicksession={(purpose) => pickSession(settingsNode.id, purpose)}
							onkeepcopy={() => keepCopy(settingsNode.id)}
							onfollowsource={() => setSessionMode(settingsNode.id, 'follow')}
							onupdatefromsession={() => updateFromSession(settingsNode.id)}
							onsaveassession={() => saveAsSession(settingsNode.id)}
							onpickmedia={pickMedia}
							onremoveasset={removeAsset}
							onclearmedia={clearMedia}
							onremove={removeSettingsNode}
							onclose={closeSettings}
							writer={writerSettings(settingsNode)}
							onwriterchange={changeWriter}
							tab={settingsTab}
							ontabchange={(id) => (settingsTab = id)}
						/>
					{/key}
				</div>
			{/if}
		</div>

		{#if phone}
			<div class="phone-dock flex flex-shrink-0 items-center gap-2 border-t border-line bg-surface-1 px-3 py-2" data-testid="elixir-dock">
				{#if latestActive}
					<Button variant="danger" size="md" icon="close" class="flex-1" loading={runBusy} disabled={runs[0]?.status === 'cancelling'} onclick={stopBrew}>Stop</Button>
				{:else}
					<Tooltip text={brewBlocked ?? 'Run every node, following the connections'} wrapperClass="flex flex-1">
						<Button variant="primary" size="md" icon="play" class="flex-1" loading={brewing} disabled={!!brewBlocked} onclick={brew}>{runs.length > 0 ? 'Brew again' : 'Brew'}</Button>
					</Tooltip>
				{/if}
			</div>
		{/if}
	</div>
	{#if sessionPicker}
		{@const target = definition.nodes.find((node) => node.id === sessionPicker?.nodeId)}
		{#if target?.preset_id && target?.mode && sessionPicker}
			<SessionPicker
				{client}
				presetId={target.preset_id}
				mode={target.mode}
				purpose={sessionPicker.purpose === 'follow' ? 'follow' : 'copy'}
				current={sessionMode(target) === 'follow' ? target.session_ref?.id ?? null : null}
				presetLabel={nodeLabel({ ...target, title: null }, presetIndex)}
				busy={importing || sessionBusy}
				onpick={onpicked}
				onclose={() => (sessionPicker = null)}
			/>
		{/if}
	{/if}
	<LaneMenu {menu} {phone} onclose={() => (menu = null)} />
	<p class="sr-only" aria-live="polite">{announcement}</p>
{/if}
</div>

