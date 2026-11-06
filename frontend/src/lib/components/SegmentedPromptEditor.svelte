<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { copyText } from '$lib/utils/clipboard';
	import { onMount, createEventDispatcher, getContext } from 'svelte';
	import type {
		ChipData,
		Prompt,
		PromptUsageHint,
		SavedSegment,
		Segment,
		SegmentCategory,
		SegmentTemplate
	} from '$lib/types/segments';
	import type { VariablesMap, VariableDef, VariableRoll } from '$lib/utils/variableDefs';
	import { conditionReadersFor } from '$lib/utils/conditionTags';
	import { segmentUsageNumbers } from '$lib/utils/variableUsageNumbering';
	import { libraryImportOutcome } from '$lib/utils/variablesTransfer';
	import { headerFit, headerStages, type HeaderActionKey, type HeaderStage } from '$lib/utils/toolbarFit';
	import { toasts } from '$lib/stores/toast';
	import { hydrateSegments } from '$lib/utils/chipParser';
	import {
		applySegmentList,
		applyTemplateSegments,
		createBlankEditorSegment,
		ensureSegmentList,
		flattenRichSegments,
		hasMeaningfulSegments,
		isSegmentEnabled,
		removeSegmentKeepingOne,
		replaceFromSavedSegment,
		savedSegmentToRichSegment,
		toEditorSegment,
		type SegmentApplyMode
	} from '$lib/utils/richSegments';
	import type { PresetSegmentTemplate } from '$lib/utils/presetSegmentTemplates';
	import type { PromptResourceSpec } from '$lib/utils/promptResources';
	import type { PromptSyntaxSpec } from '$lib/utils/promptSyntax';
	import { resolvedPromptStats, resolvedPromptTokens } from '$lib/utils/resolvedPrompt';
	import PromptSegment from './PromptSegment.svelte';
	import LiveSyncMark from '$lib/components/session/LiveSyncMark.svelte';
	import { LIVE_SYNC_TAB_CONTEXT_KEY } from '$lib/stores/liveSyncAnchors';
	import { segmentKey } from '$lib/session/sessionMerge';
	import Tooltip from './Tooltip.svelte';
	import { Button } from '$lib/components/ui';
	import PromptLibraryMenu, { type FoldedRow } from './PromptLibraryMenu.svelte';
	import SegmentListApplyModal from './modals/SegmentListApplyModal.svelte';
	import SavedSegmentSelectionModal from './modals/SavedSegmentSelectionModal.svelte';
	import SaveSegmentModal from './modals/SaveSegmentModal.svelte';
	import SavePromptModal from './modals/SavePromptModal.svelte';
	import SegmentTemplateSaveModal from './modals/SegmentTemplateSaveModal.svelte';
	import Icon from '$lib/components/Icon.svelte';

	type ApplyTarget = 'main' | 'negative';

	export let segments: Segment[] = [];
	export let isNegative = false;

	const liveSync = getContext<{ tabId: string } | undefined>(LIVE_SYNC_TAB_CONTEXT_KEY);
	export let negativeSegments: Segment[] | undefined = undefined;
	export let showPreview = true;
	export let previewSegments: Segment[] | undefined = undefined;
	export let previewText: string | undefined = undefined;
	export let negativePromptUnavailable = false;
	// The preset supports a negative, but at the current resolved
	// guidance (<= 1, NAG off) it is never sent to the model. Distinct from
	// `negativePromptUnavailable` (the preset has no negative at all); the two
	// are mutually exclusive and unavailable wins.
	export let negativeInert = false;
	export let label: string | undefined = undefined;
	export let compact = false;
	export let showLibraryActions = true;
	export let placeholder = 'Enter prompt content... (# for phrasebook)';
	export let variables: VariablesMap = {};
	export let variableRolls: Record<string, VariableRoll> = {};
	$: conditionReaders = conditionReadersFor([...segments, ...(negativeSegments ?? [])].map((segment) => segment.content));
	export let onVariableDefChange: ((name: string, def: VariableDef) => void) | undefined = undefined;
	/** Applying a library Prompt that carries variables merges them into the
	 *  tab's map (`mergeVariables(..., 'replace')`) and hands the result up here --
	 *  unlike `onVariableDefChange`, which only ever sets one name at a time. */
	export let onVariablesImport: ((merged: VariablesMap) => void) | undefined = undefined;
	export let onSourcePromptChange: ((id: string | null) => void) | undefined = undefined;
	export let onOpenVariableManager: (() => void) | undefined = undefined;
	/** Renders a Styles toolbar button (main segments only) when set — see
	 *  PromptSection.svelte's applyStyle/appliedStyleName. */
	export let onOpenStyles: (() => void) | null = null;
	export let appliedStyleName: string | null = null;
	export let activeTriggerWords: string[] = [];
	/** Segment Templates the selected preset declares, merged into the apply picker
	 *  alongside the user's own library. */
	export let presetSegmentTemplates: PresetSegmentTemplate[] = [];
	export let promptResources: PromptResourceSpec[] = [];
	export let resourceFieldValues: Record<string, unknown> = {};
	export let resourceFieldLabels: Record<string, string> = {};
	export let promptSyntax: PromptSyntaxSpec[] = [];
	// The Video Director's shot stage (StageBeat.svelte) already gives this
	// editor its own caption/card — the mock's `.composer` shell (toolbar
	// header + resolved panel) would just be a second, redundant container
	// around the same segments. `embedded` drops that chrome down to the bare
	// segment rail (`.segment-list` + its add-segment row); the `.segment-composer`
	// scope class stays on the root either way, so chip/picker styling still applies.
	export let embedded = false;
	export let plain = false;
	export let titleExtra: import('svelte').Snippet | undefined = undefined;

	$: paired = negativeSegments !== undefined;

	$: usageNumbering = segmentUsageNumbers(segments, negativeSegments || []);
	// aria-label for the sections role="list" — kept stable independent of the
	// visible header copy below.
	$: mainLabel = label || (isNegative ? 'Negative segments' : 'Positive segments');
	$: variableCount = Object.keys(variables || {}).length;

	$: headerWord = label || (isNegative && !paired ? 'Negative' : 'Prompt');

	const dispatch = createEventDispatcher();

	// State
	let showPromptApplyModal = false;
	let showTemplateApplyModal = false;
	let applyTarget: ApplyTarget = 'main';

	let showSavePromptModal = false;
	let savePromptTarget: ApplyTarget = 'main';

	let showSaveTemplateModal = false;
	let saveTemplateTarget: ApplyTarget = 'main';

	let saveSegmentId: string | null = null;
	let saveSegmentTarget: ApplyTarget = 'main';

	type SavedSegmentFlow =
		| { mode: 'replace'; target: ApplyTarget; segmentId: string }
		| { mode: 'insert'; target: ApplyTarget }
		| null;
	let savedSegmentFlow: SavedSegmentFlow = null;

	const EMPTY_STAGE: HeaderStage = { wrap: false, icons: [], folded: [] };
	let mainStage: HeaderStage = EMPTY_STAGE;

	let resolvedOpen = true;
	let copiedTarget: ApplyTarget | null = null;

	let lastSegmentsJson = '';
	let lastNegativeSegmentsJson = '';
	let isHydrating = false;

	function getList(target: ApplyTarget): Segment[] {
		return target === 'negative' ? negativeSegments || [] : segments;
	}

	function commitList(target: ApplyTarget, next: Segment[]) {
		const ensured = ensureSegmentList(next);
		if (target === 'negative') {
			negativeSegments = ensured;
			dispatch('negativeSegmentsChange', ensured);
		} else {
			segments = ensured;
			dispatch('segmentsChange', ensured);
		}
	}

	// `isHydrating` (shared across both targets) already keeps two
	// hydrateExternal calls from ever running concurrently -- but it also
	// makes the reactive triggers below SKIP re-arming while one is in
	// flight, so a caller that hands down a fresh `segments`/`negativeSegments`
	// array every edit (StageShot re-derives one from `doc` on every
	// keystroke) can still have this hydration's content move out from under
	// it before it resolves. `{segments,negativeSegments}Json` (below) are
	// this target's content fingerprint at any instant; comparing the
	// fingerprint this call started from against the live one at resolve
	// time is the epoch check -- a mismatch means newer text arrived while
	// this hydration was in flight, so its (now stale) result must never be
	// committed over it. Discarding (not committing) also lets the reactive
	// trigger re-arm for the truly current content once `isHydrating` drops.
	async function hydrateExternal(target: ApplyTarget) {
		const startedFromJson = target === 'negative' ? negativeSegmentsJson : segmentsJson;
		isHydrating = true;
		try {
			const list = getList(target);
			const hydrated = await hydrateSegments(list);
			const currentJson = target === 'negative' ? negativeSegmentsJson : segmentsJson;
			if (currentJson !== startedFromJson) return; // superseded by a newer edit
			const hasNewChips = hydrated.some((seg, i) => {
				const originalChipCount = Object.keys(list[i]?.chips || {}).length;
				const newChipCount = Object.keys(seg.chips || {}).length;
				return newChipCount > originalChipCount;
			});
			if (hasNewChips) commitList(target, hydrated);
		} finally {
			isHydrating = false;
		}
	}

	// Track external segment updates (e.g. session load) by JSON diff, not reference.
	$: segmentsJson = JSON.stringify(
		segments.map((segment) => ({
			id: segment.id,
			content: segment.content,
			chipCount: Object.keys(segment.chips || {}).length
		}))
	);
	$: if (segmentsJson !== lastSegmentsJson && !isHydrating) {
		lastSegmentsJson = segmentsJson;
		if (segments.length > 0 && segments.some((s) => s.content || s.id)) hydrateExternal('main');
	}

	$: negativeSegmentsJson = paired
		? JSON.stringify(
				(negativeSegments || []).map((segment) => ({
					id: segment.id,
					content: segment.content,
					chipCount: Object.keys(segment.chips || {}).length
				}))
			)
		: '';
	$: if (paired && negativeSegmentsJson !== lastNegativeSegmentsJson && !isHydrating) {
		lastNegativeSegmentsJson = negativeSegmentsJson;
		const list = negativeSegments || [];
		if (list.length > 0 && list.some((s) => s.content || s.id)) hydrateExternal('negative');
	}

	onMount(() => {
		if (segments.length === 0) commitList('main', []);
		if (paired && (negativeSegments || []).length === 0) commitList('negative', []);
	});

	$: hasMainContent = flattenRichSegments(segments).length > 0;
	$: hasNegativeContent = paired ? flattenRichSegments(negativeSegments || []).length > 0 : false;
	$: segmentToSave = saveSegmentId
		? getList(saveSegmentTarget).find((segment) => segment.id === saveSegmentId) || null
		: null;
	$: savePromptUsageHint = ((): PromptUsageHint =>
		savePromptTarget === 'negative' || isNegative ? 'negative' : 'positive')();

	function addSegment(target: ApplyTarget) {
		commitList(target, [...getList(target), createBlankEditorSegment()]);
	}

	function removeSegment(target: ApplyTarget, id: string) {
		commitList(target, removeSegmentKeepingOne(getList(target), id));
	}

	function moveSegment(target: ApplyTarget, id: string, direction: 'up' | 'down') {
		const list = getList(target);
		const index = list.findIndex((s) => s.id === id);
		if (index === -1) return;

		const newIndex = direction === 'up' ? index - 1 : index + 1;
		if (newIndex < 0 || newIndex >= list.length) return;

		const newList = [...list];
		[newList[index], newList[newIndex]] = [newList[newIndex], newList[index]];
		commitList(target, newList);
	}

	function handleSegmentDrop(target: ApplyTarget, draggedId: string, targetId: string, position: 'top' | 'bottom') {
		const list = getList(target);
		const draggedIndex = list.findIndex((s) => s.id === draggedId);
		const targetIndex = list.findIndex((s) => s.id === targetId);

		if (draggedIndex === -1 || targetIndex === -1) return;
		if (draggedIndex === targetIndex) return;

		const newList = [...list];
		const [draggedSegment] = newList.splice(draggedIndex, 1);

		let newIndex = targetIndex;
		if (draggedIndex < targetIndex) {
			newIndex = position === 'top' ? targetIndex - 1 : targetIndex;
		} else {
			newIndex = position === 'top' ? targetIndex : targetIndex + 1;
		}

		newList.splice(newIndex, 0, draggedSegment);
		commitList(target, newList);
	}

	function duplicateSegment(target: ApplyTarget, id: string) {
		const list = getList(target);
		const segment = list.find((s) => s.id === id);
		if (!segment) return;

		const newSegment = toEditorSegment(segment);
		const sourceName = segment.name || segment.title;
		if (sourceName) newSegment.name = `${sourceName} (Copy)`;

		const index = list.findIndex((s) => s.id === id);
		commitList(target, [...list.slice(0, index + 1), newSegment, ...list.slice(index + 1)]);
	}

	function toggleSegmentDisabled(target: ApplyTarget, id: string) {
		commitList(
			target,
			getList(target).map((s) => {
				if (s.id === id) {
					const enabled = !isSegmentEnabled(s);
					return { ...s, enabled, isDisabled: !enabled };
				}
				return s;
			})
		);
	}

	function handleSegmentUpdate(
		target: ApplyTarget,
		id: string,
		detail: { value: string; chips: Record<string, ChipData>; resources?: Segment['resources'] }
	) {
		commitList(
			target,
			getList(target).map((s) =>
				s.id === id ? { ...s, content: detail.value, chips: detail.chips, resources: detail.resources } : s
			)
		);
	}

	function handleMetadataUpdate(
		target: ApplyTarget,
		id: string,
		metadata: Pick<Segment, 'name' | 'color' | 'description' | 'prefix' | 'suffix'>
	) {
		commitList(
			target,
			getList(target).map((segment) =>
				segment.id === id ? { ...segment, ...metadata, title: undefined } : segment
			)
		);
	}

	function openLibraryInsert(target: ApplyTarget) {
		savedSegmentFlow = { mode: 'insert', target };
	}

	function openReplaceFromSaved(target: ApplyTarget, segmentId: string) {
		savedSegmentFlow = { mode: 'replace', target, segmentId };
	}

	function handleSavedSegmentSelect(detail: { savedSegment: SavedSegment; category?: SegmentCategory }) {
		if (!savedSegmentFlow) return;
		const flow = savedSegmentFlow;
		if (flow.mode === 'replace') {
			commitList(
				flow.target,
				replaceFromSavedSegment(getList(flow.target), flow.segmentId, detail.savedSegment, detail.category)
			);
		} else {
			const richCopy = savedSegmentToRichSegment(detail.savedSegment, detail.category);
			commitList(flow.target, applySegmentList(getList(flow.target), [richCopy], 'append'));
		}
		savedSegmentFlow = null;
	}

	function openTemplateApply(target: ApplyTarget) {
		applyTarget = target;
		showTemplateApplyModal = true;
	}

	function openPromptApply(target: ApplyTarget) {
		applyTarget = target;
		showPromptApplyModal = true;
	}

	function handleLibraryApply(detail: { item: Prompt | SegmentTemplate; mode: SegmentApplyMode }, kind: 'prompt' | 'template') {
		const next =
			kind === 'template'
				? applyTemplateSegments(getList(applyTarget), detail.item as SegmentTemplate, detail.mode)
				: applySegmentList(getList(applyTarget), detail.item.segments, detail.mode);
		commitList(applyTarget, next);
		if (kind === 'template' && detail.mode === 'replace') onSourcePromptChange?.(null);
		if (kind === 'prompt') {
			onSourcePromptChange?.(detail.item.id);
			const imported = (detail.item as Prompt).variables;
			const importedCount = imported ? Object.keys(imported).length : 0;
			if (importedCount > 0 && onVariablesImport) {
				const outcome = libraryImportOutcome(variables, imported as VariablesMap);
				onVariablesImport(outcome.variables);
				toasts.success(outcome.successMessage);
				if (outcome.warningMessage) toasts.warning(outcome.warningMessage);
			}
		}
		showPromptApplyModal = false;
		showTemplateApplyModal = false;
	}

	function openSavePrompt(target: ApplyTarget) {
		savePromptTarget = target;
		showSavePromptModal = true;
	}

	function openSaveTemplate(target: ApplyTarget) {
		saveTemplateTarget = target;
		showSaveTemplateModal = true;
	}

	async function handleCopyPrompt(target: ApplyTarget) {
		const prompt = flattenRichSegments(getList(target));
		if (!prompt) return;

		const ok = await copyText(prompt);
		if (ok) {
			copiedTarget = target;
			setTimeout(() => (copiedTarget = copiedTarget === target ? null : copiedTarget), 2000);
		} else {
			logger.error('Failed to copy prompt');
		}
	}

	$: mainActions = [
		...(onOpenStyles ? ['styles'] : []),
		...(onOpenVariableManager ? ['variables'] : [])
	] as HeaderActionKey[];
	$: mainStages = headerStages({ actions: mainActions, tabs: !!titleExtra, compact });
	$: negativeStages = headerStages({ actions: [], tabs: false, compact });

	function foldedRowsFor(folded: string[], styleName: string | null, variablesTotal: number): FoldedRow[] {
		const rows: Record<string, FoldedRow> = {
			styles: {
				key: 'styles',
				icon: 'sparkles',
				label: 'Styles',
				description: 'Apply a preset style',
				state: styleName ?? undefined,
				run: () => onOpenStyles?.()
			},
			variables: {
				key: 'variables',
				icon: 'braces',
				label: 'Variables',
				description: 'Manage prompt variables',
				state: variablesTotal > 0 ? String(variablesTotal) : undefined,
				run: () => onOpenVariableManager?.()
			}
		};
		return ['styles', 'variables'].filter((key) => folded.includes(key)).map((key) => rows[key]);
	}

	$: mainFolded = foldedRowsFor(mainStage.folded, appliedStyleName, variableCount);
	$: mainFoldedState = mainFolded.some((row) => row.state !== undefined);

	$: previewSource = previewSegments ?? segments;
	$: hasPreviewContent = (previewText ?? flattenRichSegments(previewSource)).length > 0;
	$: resolvedTokens = showPreview && resolvedOpen ? resolvedPromptTokens(previewSource, previewText) : [];
	$: resolvedStats = showPreview ? resolvedPromptStats(previewSource, previewText) : { chars: 0 };
</script>

<div class="prompt-editor segment-composer min-w-0" class:compact>
	{#snippet mainRail()}
		<div class="segment-lane">
		<div role="list" aria-label={mainLabel} class="segment-list">
			{#each segments as segment, index (segment.id)}
				<LiveSyncMark
					tabId={liveSync?.tabId}
					fieldKey={segmentKey(isNegative && !paired ? 'negativePromptSegments' : 'promptSegments', segment.id)}
				>
					<PromptSegment
						{segment}
						{index}
						total={segments.length}
						isNegative={isNegative && !paired}
						{compact}
						{placeholder}
						{variables}
						{variableRolls}
						{conditionReaders}
						variableUsageNumbers={usageNumbering[index] || []}
						{onVariableDefChange}
						{onOpenVariableManager}
						{activeTriggerWords}
						{promptResources}
						{resourceFieldValues}
						{resourceFieldLabels}
						{promptSyntax}
						on:change={(e) => handleSegmentUpdate('main', segment.id, e.detail)}
						on:metadataChange={(e) => handleMetadataUpdate('main', segment.id, e.detail)}
						on:remove={() => removeSegment('main', segment.id)}
						on:duplicate={() => duplicateSegment('main', segment.id)}
						on:toggleDisabled={() => toggleSegmentDisabled('main', segment.id)}
						on:moveUp={() => moveSegment('main', segment.id, 'up')}
						on:moveDown={() => moveSegment('main', segment.id, 'down')}
						on:saveAsSegment={() => {
							saveSegmentTarget = 'main';
							saveSegmentId = segment.id;
						}}
						on:replaceFromSaved={() => openReplaceFromSaved('main', segment.id)}
						on:drop={(e) => handleSegmentDrop('main', e.detail.draggedId, e.detail.targetId, e.detail.position)}
					/>
				</LiveSyncMark>
			{/each}
		</div>

		<button type="button" class="add-segment add-row" on:click={() => addSegment('main')}>
			<Icon name="composer-plus" className="icon" strokeWidth={1.7} />
			<span>Add segment</span>
		</button>
		</div>
	{/snippet}

	{#if embedded}
		{@render mainRail()}
	{:else}
		<section class="composer edge-inline" class:compact class:plain>
			<header
				class="composer-toolbar section-header prompt-header"
				class:has-tabs={!!titleExtra}
				use:headerFit={{ stages: mainStages, onChange: (stage) => (mainStage = stage) }}
			>
				{#if titleExtra}
					{@render titleExtra()}
				{:else}
					<strong class="composer-title section-title prompt-title">{headerWord}</strong>
				{/if}

				{#if !paired && isNegative && negativePromptUnavailable}
					<span class="inline-note-warning">Not used by this preset</span>
				{/if}

				<div class="toolbar-spacer"></div>

				<div class="header-actions">
					{#if onOpenStyles}
						<span class="toolbar-slot" data-toolbar-action="styles" data-header-action="styles">
							<Tooltip text={appliedStyleName ? `Style: ${appliedStyleName}` : 'Apply a preset style'} position="top">
								<Button
									size="sm"
									variant="secondary"
									icon="sparkles"
									class={appliedStyleName ? 'styles-applied' : ''}
									ariaLabel={appliedStyleName ? `Style: ${appliedStyleName}` : 'Styles'}
									onclick={onOpenStyles}
								>
									<span class="header-label">{appliedStyleName ? `Style: ${appliedStyleName}` : 'Styles'}</span>
								</Button>
							</Tooltip>
						</span>
					{/if}

					{#if onOpenVariableManager}
						<span class="toolbar-slot" data-toolbar-action="variables" data-header-action="variables">
							<Tooltip text={variableCount > 0 ? `Manage prompt variables · ${variableCount}` : 'Manage prompt variables'} position="top">
								<Button
									size="sm"
									variant="secondary"
									icon="braces"
									ariaLabel={variableCount > 0 ? `Variables (${variableCount})` : 'Variables'}
									onclick={onOpenVariableManager}
								>
									<span class="header-label">Variables</span>
									{#if variableCount > 0}
										<span class="font-mono tabular-nums text-sm text-signal">{variableCount}</span>
									{/if}
								</Button>
							</Tooltip>
						</span>
					{/if}

					{#if showLibraryActions || hasMainContent || mainFolded.length > 0}
						<span class="toolbar-slot" data-toolbar-action="library" data-header-action="library">
							<PromptLibraryMenu
								library={showLibraryActions}
								folded={mainFolded}
								hasContent={hasMainContent}
								copied={copiedTarget === 'main'}
								showDot={mainFoldedState}
								onInsertPrompt={() => openPromptApply('main')}
								onInsertSegment={() => openLibraryInsert('main')}
								onInsertTemplate={() => openTemplateApply('main')}
								onSavePrompt={() => openSavePrompt('main')}
								onSaveTemplate={() => openSaveTemplate('main')}
								onCopy={() => handleCopyPrompt('main')}
							/>
						</span>
					{/if}
				</div>
			</header>

			{@render mainRail()}

			{#if showPreview}
				<section class="resolved" class:open={resolvedOpen}>
					<div class="resolved-head">
						<button
							type="button"
							class="resolved-toggle"
							aria-expanded={resolvedOpen}
							on:click={() => (resolvedOpen = !resolvedOpen)}
						>
							<Icon name="composer-chevron-down" className="icon" strokeWidth={1.7} />
							<span class="resolved-title">What the model receives</span>
						</button>
						<span class="resolved-stats font-mono tabular-nums">
							{resolvedStats.chars} chars
						</span>
						{#if hasPreviewContent}
							<button type="button" class="resolved-copy small-button" on:click={() => handleCopyPrompt('main')}>
								{copiedTarget === 'main' ? 'Copied' : 'Copy'}
							</button>
						{/if}
					</div>

					{#if resolvedOpen}
						<div class="resolved-body font-mono">
							{#if resolvedTokens.length}
								{#each resolvedTokens as token}
									{#if token.kind === 'value'}
										<mark class="resolved-value">{token.text}</mark>
									{:else if token.kind === 'emphasis'}
										<span class="resolved-emphasis">{token.text}</span>
									{:else if token.kind === 'muted'}
										<span class="resolved-muted">{token.text}</span>
									{:else}{token.text}{/if}
								{/each}
							{:else}
								<span class="resolved-empty">Nothing yet — the enabled segments above are empty.</span>
							{/if}
						</div>
					{/if}
				</section>
			{/if}
		</section>
	{/if}

	{#if paired}
		{#snippet negativeRail()}
			<div class="segment-lane">
			<div role="list" aria-label="Negative segments" class="segment-list negative-list">
				{#each negativeSegments || [] as segment, index (segment.id)}
					<LiveSyncMark tabId={liveSync?.tabId} fieldKey={segmentKey('negativePromptSegments', segment.id)}>
						<PromptSegment
							{segment}
							{index}
							total={(negativeSegments || []).length}
							isNegative={true}
							{compact}
							placeholder="What should never appear… (# for phrasebook)"
							{variables}
							{variableRolls}
							{conditionReaders}
							variableUsageNumbers={usageNumbering[segments.length + index] || []}
							{onVariableDefChange}
							{onOpenVariableManager}
							{activeTriggerWords}
							{promptResources}
							{resourceFieldValues}
							{resourceFieldLabels}
							{promptSyntax}
							on:change={(e) => handleSegmentUpdate('negative', segment.id, e.detail)}
							on:metadataChange={(e) => handleMetadataUpdate('negative', segment.id, e.detail)}
							on:remove={() => removeSegment('negative', segment.id)}
							on:duplicate={() => duplicateSegment('negative', segment.id)}
							on:toggleDisabled={() => toggleSegmentDisabled('negative', segment.id)}
							on:moveUp={() => moveSegment('negative', segment.id, 'up')}
							on:moveDown={() => moveSegment('negative', segment.id, 'down')}
							on:saveAsSegment={() => {
								saveSegmentTarget = 'negative';
								saveSegmentId = segment.id;
							}}
							on:replaceFromSaved={() => openReplaceFromSaved('negative', segment.id)}
							on:drop={(e) => handleSegmentDrop('negative', e.detail.draggedId, e.detail.targetId, e.detail.position)}
						/>
					</LiveSyncMark>
				{/each}
			</div>

			<button type="button" class="add-segment add-row" on:click={() => addSegment('negative')}>
				<Icon name="composer-plus" className="icon" strokeWidth={1.7} />
				<span>Add segment</span>
			</button>
			</div>
		{/snippet}

		{#if embedded}
			{@render negativeRail()}
		{:else if !negativeInert}
			<section class="composer edge-inline negative-composer" class:compact class:plain>
				<header
					class="composer-toolbar section-header negative-header prompt-header"
					use:headerFit={{ stages: negativeStages }}
				>
					<strong class="composer-title negative section-title prompt-title">Negative</strong>

					<div class="toolbar-spacer"></div>

					<div class="header-actions">
						{#if showLibraryActions || hasNegativeContent}
							<span class="toolbar-slot" data-toolbar-action="library" data-header-action="library">
								<PromptLibraryMenu
									negative
									library={showLibraryActions}
									hasContent={hasNegativeContent}
									copied={copiedTarget === 'negative'}
									onInsertPrompt={() => openPromptApply('negative')}
									onInsertSegment={() => openLibraryInsert('negative')}
									onInsertTemplate={() => openTemplateApply('negative')}
									onSavePrompt={() => openSavePrompt('negative')}
									onSaveTemplate={() => openSaveTemplate('negative')}
									onCopy={() => handleCopyPrompt('negative')}
								/>
							</span>
						{/if}
					</div>
				</header>

				{#if negativePromptUnavailable}
					<p class="negative-note inline-warning" data-testid="negative-note">Not used by this preset</p>
				{/if}

				{@render negativeRail()}
			</section>
		{/if}
	{/if}
</div>

<SegmentListApplyModal
	isOpen={showPromptApplyModal}
	kind="prompt"
	targetHasMeaningfulContent={hasMeaningfulSegments(getList(applyTarget))}
	on:close={() => (showPromptApplyModal = false)}
	on:apply={(event) => handleLibraryApply(event.detail, 'prompt')}
/>

<SegmentListApplyModal
	isOpen={showTemplateApplyModal}
	kind="template"
	presetTemplates={presetSegmentTemplates}
	targetHasMeaningfulContent={hasMeaningfulSegments(getList(applyTarget))}
	on:close={() => (showTemplateApplyModal = false)}
	on:apply={(event) => handleLibraryApply(event.detail, 'template')}
/>

<SavedSegmentSelectionModal
	isOpen={savedSegmentFlow !== null}
	title={savedSegmentFlow?.mode === 'insert' ? 'Insert saved Segment' : 'Replace from saved Segment'}
	on:close={() => (savedSegmentFlow = null)}
	on:select={(event) => handleSavedSegmentSelect(event.detail)}
/>

{#if segmentToSave}
	<SaveSegmentModal
		isOpen={saveSegmentId !== null}
		segment={segmentToSave}
		{promptResources}
		{resourceFieldValues}
		on:close={() => (saveSegmentId = null)}
		on:saved={() => (saveSegmentId = null)}
	/>
{/if}

<SavePromptModal
	isOpen={showSavePromptModal}
	segments={getList(savePromptTarget)}
	usageHint={savePromptUsageHint}
	{variables}
	on:close={() => (showSavePromptModal = false)}
	on:saved={(event) => {
		showSavePromptModal = false;
		onSourcePromptChange?.(event.detail.id);
	}}
/>

<SegmentTemplateSaveModal
	isOpen={showSaveTemplateModal}
	segments={getList(saveTemplateTarget)}
	onClose={() => (showSaveTemplateModal = false)}
	onSaved={() => (showSaveTemplateModal = false)}
/>

<style>
	.prompt-editor {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.negative-composer {
		margin-top: 0.125rem;
	}

	.inline-warning {
		font-size: 0.75rem;
		color: rgb(var(--warning));
	}

	.negative-note {
		margin: 0;
		padding: 0.375rem 0.875rem 0;
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}

	.composer.plain .negative-note {
		padding-left: 0;
		padding-right: 0;
	}

	.inline-note-warning {
		flex: none;
		white-space: nowrap;
		border-radius: 0.25rem;
		border: 1px solid rgb(var(--warning) / 0.25);
		background-color: rgb(var(--warning) / 0.1);
		padding: 0.125rem 0.375rem;
		font-size: 0.75rem;
		font-weight: 500;
		color: rgb(var(--warning));
	}

	.segment-composer :global(.styles-applied) {
		color: rgb(var(--signal));
		box-shadow: inset 0 0 0 1px rgb(var(--signal));
	}
</style>
