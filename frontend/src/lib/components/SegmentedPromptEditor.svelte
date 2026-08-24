<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { copyText } from '$lib/utils/clipboard';
	import { onMount, createEventDispatcher, tick } from 'svelte';
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
	import { resolvedPromptStats, resolvedPromptTokens } from '$lib/utils/resolvedPrompt';
	import PromptSegment from './PromptSegment.svelte';
	import Tooltip from './Tooltip.svelte';
	import Icon from './Icon.svelte';
	import { Button } from '$lib/components/ui';
	import SegmentListApplyModal from './modals/SegmentListApplyModal.svelte';
	import SavedSegmentSelectionModal from './modals/SavedSegmentSelectionModal.svelte';
	import SaveSegmentModal from './modals/SaveSegmentModal.svelte';
	import SavePromptModal from './modals/SavePromptModal.svelte';

	type ApplyTarget = 'main' | 'negative';

	// Props
	export let segments: Segment[] = [];
	export let isNegative = false;
	export let negativeSegments: Segment[] | undefined = undefined;
	export let showPreview = true;
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
	export let onVariableDefChange: ((name: string, def: VariableDef) => void) | undefined = undefined;
	export let onOpenVariableManager: (() => void) | undefined = undefined;
	export let activeTriggerWords: string[] = [];

	$: paired = negativeSegments !== undefined;
	// aria-label for the sections role="list" — kept stable independent of the
	// visible header copy below.
	$: mainLabel = label || (isNegative ? 'Negative segments' : 'Positive segments');
	$: variableCount = Object.keys(variables || {}).length;

	function segmentCountLabel(count: number): string {
		return `${count} ${count === 1 ? 'segment' : 'segments'}`;
	}

	$: headerWord = label || (isNegative && !paired ? 'Negative' : 'Prompt');

	const dispatch = createEventDispatcher();

	// State
	let showPromptApplyModal = false;
	let showTemplateApplyModal = false;
	let applyTarget: ApplyTarget = 'main';

	let showSavePromptModal = false;
	let savePromptTarget: ApplyTarget = 'main';

	let saveSegmentId: string | null = null;
	let saveSegmentTarget: ApplyTarget = 'main';

	type SavedSegmentFlow =
		| { mode: 'replace'; target: ApplyTarget; segmentId: string }
		| { mode: 'insert'; target: ApplyTarget }
		| null;
	let savedSegmentFlow: SavedSegmentFlow = null;

	let mainMoreOpen = false;
	let mainMoreRoot: HTMLDivElement;
	let mainMoreTrigger: HTMLButtonElement;
	let negativeMoreOpen = false;
	let negativeMoreRoot: HTMLDivElement;
	let negativeMoreTrigger: HTMLButtonElement;

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

	function toggleSegmentBreak(target: ApplyTarget, id: string) {
		commitList(
			target,
			getList(target).map((segment) =>
				segment.id === id
					? { ...segment, type: segment.type === 'break' ? 'content' : 'break' }
					: segment
			)
		);
	}

	function handleSegmentUpdate(
		target: ApplyTarget,
		id: string,
		detail: { value: string; chips: Record<string, ChipData> }
	) {
		commitList(
			target,
			getList(target).map((s) => (s.id === id ? { ...s, content: detail.value, chips: detail.chips } : s))
		);
	}

	function handleMetadataUpdate(
		target: ApplyTarget,
		id: string,
		metadata: Pick<Segment, 'name' | 'color' | 'description'>
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
		showPromptApplyModal = false;
		showTemplateApplyModal = false;
	}

	function openSavePrompt(target: ApplyTarget) {
		savePromptTarget = target;
		showSavePromptModal = true;
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

	async function toggleMainMore() {
		mainMoreOpen = !mainMoreOpen;
		if (mainMoreOpen) {
			await tick();
			mainMoreRoot?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
		}
	}

	async function toggleNegativeMore() {
		negativeMoreOpen = !negativeMoreOpen;
		if (negativeMoreOpen) {
			await tick();
			negativeMoreRoot?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
		}
	}

	function handleOutsidePointerDown(event: PointerEvent) {
		if (mainMoreOpen && mainMoreRoot && !mainMoreRoot.contains(event.target as Node)) mainMoreOpen = false;
		if (negativeMoreOpen && negativeMoreRoot && !negativeMoreRoot.contains(event.target as Node)) negativeMoreOpen = false;
	}

	function handleOutsideKeydown(event: KeyboardEvent) {
		if (event.key !== 'Escape') return;
		if (mainMoreOpen) mainMoreOpen = false;
		if (negativeMoreOpen) negativeMoreOpen = false;
	}

	function runMainMore(action: () => void) {
		mainMoreOpen = false;
		action();
	}

	function runNegativeMore(action: () => void) {
		negativeMoreOpen = false;
		action();
	}

	$: negativeCount = (negativeSegments || []).length;
	$: resolvedTokens = showPreview && resolvedOpen ? resolvedPromptTokens(segments) : [];
	$: resolvedStats = showPreview ? resolvedPromptStats(segments) : { chars: 0, breaks: 0 };
</script>

<svelte:window on:pointerdown={handleOutsidePointerDown} on:keydown={handleOutsideKeydown} />

<div class="prompt-editor min-w-0" class:compact>
	<div class="section-header">
		<span class="section-title">{headerWord}</span>
		<span class="section-count font-mono tabular-nums">{segmentCountLabel(segments.length)}</span>

		{#if !paired && isNegative && negativePromptUnavailable}
			<span class="inline-note-warning">Not used by this preset</span>
		{/if}

		<div class="header-spacer"></div>

		{#if showLibraryActions}
			<Tooltip text="Insert a saved Segment" position="top">
				<Button variant="ghost" size="xs" icon="library" class="header-btn" onclick={() => openLibraryInsert('main')}>
					{#if !compact}<span>Library</span>{/if}
				</Button>
			</Tooltip>
			<Tooltip text="Apply a Segment Template" position="top">
				<Button variant="ghost" size="xs" icon="layout-template" class="header-btn" onclick={() => openTemplateApply('main')}>
					{#if !compact}<span>Template</span>{/if}
				</Button>
			</Tooltip>
		{/if}

		{#if onOpenVariableManager}
			<Tooltip text="Manage prompt variables" position="top">
				<Button variant="ghost" size="xs" icon="braces" class="header-btn" onclick={onOpenVariableManager}>
					{#if !compact}<span>Variables</span>{/if}
					{#if variableCount > 0}
						<span class="count-badge font-mono tabular-nums">{variableCount}</span>
					{/if}
				</Button>
			</Tooltip>
		{/if}

		{#if showLibraryActions || hasMainContent}
			<div class="relative" bind:this={mainMoreRoot}>
				<Tooltip text="More prompt actions" position="top">
					<button
						type="button"
						class="header-icon-btn"
						class:active={mainMoreOpen}
						bind:this={mainMoreTrigger}
						aria-haspopup="menu"
						aria-expanded={mainMoreOpen}
						aria-label="More prompt actions"
						on:click={toggleMainMore}
					>
						<Icon name="more" className="h-4 w-4" />
					</button>
				</Tooltip>
				{#if mainMoreOpen}
					<div class="header-menu" role="menu" aria-label="More prompt actions">
						{#if showLibraryActions}
							<button type="button" role="menuitem" on:click={() => runMainMore(() => openPromptApply('main'))}>
								<Icon name="book-open" className="h-4 w-4 flex-shrink-0" />
								<span>Apply Prompt</span>
							</button>
							<button type="button" role="menuitem" on:click={() => runMainMore(() => openSavePrompt('main'))}>
								<Icon name="save" className="h-4 w-4 flex-shrink-0" />
								<span>Save as Prompt</span>
							</button>
						{/if}
						{#if hasMainContent}
							<button type="button" role="menuitem" on:click={() => runMainMore(() => handleCopyPrompt('main'))}>
								<Icon name={copiedTarget === 'main' ? 'check' : 'copy'} className="h-4 w-4 flex-shrink-0" />
								<span>{copiedTarget === 'main' ? 'Copied' : 'Copy prompt'}</span>
							</button>
						{/if}
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<div role="list" aria-label={mainLabel} class="segment-list">
		{#each segments as segment, index (segment.id)}
			<PromptSegment
				{segment}
				{index}
				total={segments.length}
				isNegative={isNegative && !paired}
				{compact}
				{placeholder}
				{variables}
				{variableRolls}
				{onVariableDefChange}
				{onOpenVariableManager}
				{activeTriggerWords}
				on:change={(e) => handleSegmentUpdate('main', segment.id, e.detail)}
				on:metadataChange={(e) => handleMetadataUpdate('main', segment.id, e.detail)}
				on:remove={() => removeSegment('main', segment.id)}
				on:duplicate={() => duplicateSegment('main', segment.id)}
				on:toggleDisabled={() => toggleSegmentDisabled('main', segment.id)}
				on:toggleBreak={() => toggleSegmentBreak('main', segment.id)}
				on:moveUp={() => moveSegment('main', segment.id, 'up')}
				on:moveDown={() => moveSegment('main', segment.id, 'down')}
				on:saveAsSegment={() => {
					saveSegmentTarget = 'main';
					saveSegmentId = segment.id;
				}}
				on:replaceFromSaved={() => openReplaceFromSaved('main', segment.id)}
				on:drop={(e) => handleSegmentDrop('main', e.detail.draggedId, e.detail.targetId, e.detail.position)}
			/>
		{/each}
	</div>

	<button type="button" class="add-row" on:click={() => addSegment('main')}>
		<Icon name="plus" className="h-3.5 w-3.5" />
		<span>Add segment</span>
	</button>

	{#if showPreview}
		<div class="resolved-divider" aria-hidden="true">
			<span class="divider-rule"></span>
			<span class="divider-label font-mono">resolved</span>
			<span class="divider-rule"></span>
		</div>

		<div class="resolved-panel">
			<div class="resolved-head">
				<button
					type="button"
					class="resolved-toggle"
					aria-expanded={resolvedOpen}
					on:click={() => (resolvedOpen = !resolvedOpen)}
				>
					<Icon name="chevron-down" className="h-3.5 w-3.5 flex-shrink-0 transition-transform {resolvedOpen ? 'rotate-180' : ''}" />
					<span class="resolved-title">What the model receives</span>
				</button>
				<span class="resolved-stats font-mono tabular-nums">
					{resolvedStats.chars} chars · {resolvedStats.breaks} {resolvedStats.breaks === 1 ? 'break' : 'breaks'}
				</span>
				<div class="header-spacer"></div>
				{#if hasMainContent}
					<button type="button" class="resolved-copy" on:click={() => handleCopyPrompt('main')}>
						{copiedTarget === 'main' ? 'Copied' : 'Copy'}
					</button>
				{/if}
			</div>

			{#if resolvedOpen}
				<div class="resolved-body font-mono">
					{#if resolvedTokens.length}
						{#each resolvedTokens as token}
							{#if token.kind === 'break'}
								<span class="resolved-break">{token.text}</span>
							{:else if token.kind === 'value'}
								<span class="resolved-value">{token.text}</span>
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
		</div>
	{/if}

	{#if paired}
		<div class="section-header negative-header">
			<span class="section-title negative">Negative</span>
			<span class="section-count font-mono tabular-nums">{segmentCountLabel(negativeCount)}</span>

			{#if negativePromptUnavailable}
				<span class="inline-note-warning">Not used by this preset</span>
			{:else if negativeInert}
				<span class="inline-warning">Not applied at current guidance</span>
			{/if}

			<div class="header-spacer"></div>

			{#if showLibraryActions}
				<Tooltip text="Insert a saved Segment" position="top">
					<Button variant="ghost" size="xs" icon="library" class="header-btn" onclick={() => openLibraryInsert('negative')}>
						{#if !compact}<span>Library</span>{/if}
					</Button>
				</Tooltip>
				<Tooltip text="Apply a Segment Template" position="top">
					<Button variant="ghost" size="xs" icon="layout-template" class="header-btn" onclick={() => openTemplateApply('negative')}>
						{#if !compact}<span>Template</span>{/if}
					</Button>
				</Tooltip>
			{/if}

			{#if showLibraryActions || hasNegativeContent}
				<div class="relative" bind:this={negativeMoreRoot}>
					<Tooltip text="More negative prompt actions" position="top">
						<button
							type="button"
							class="header-icon-btn"
							class:active={negativeMoreOpen}
							bind:this={negativeMoreTrigger}
							aria-haspopup="menu"
							aria-expanded={negativeMoreOpen}
							aria-label="More negative prompt actions"
							on:click={toggleNegativeMore}
						>
							<Icon name="more" className="h-4 w-4" />
						</button>
					</Tooltip>
					{#if negativeMoreOpen}
						<div class="header-menu" role="menu" aria-label="More negative prompt actions">
							{#if showLibraryActions}
								<button type="button" role="menuitem" on:click={() => runNegativeMore(() => openPromptApply('negative'))}>
									<Icon name="book-open" className="h-4 w-4 flex-shrink-0" />
									<span>Apply Prompt</span>
								</button>
								<button type="button" role="menuitem" on:click={() => runNegativeMore(() => openSavePrompt('negative'))}>
									<Icon name="save" className="h-4 w-4 flex-shrink-0" />
									<span>Save as Prompt</span>
								</button>
							{/if}
							{#if hasNegativeContent}
								<button type="button" role="menuitem" on:click={() => runNegativeMore(() => handleCopyPrompt('negative'))}>
									<Icon name={copiedTarget === 'negative' ? 'check' : 'copy'} className="h-4 w-4 flex-shrink-0" />
									<span>{copiedTarget === 'negative' ? 'Copied' : 'Copy prompt'}</span>
								</button>
							{/if}
						</div>
					{/if}
				</div>
			{/if}
		</div>

		<div role="list" aria-label="Negative segments" class="segment-list negative-list">
			{#each negativeSegments || [] as segment, index (segment.id)}
				<PromptSegment
					{segment}
					{index}
					total={(negativeSegments || []).length}
					isNegative={true}
					{compact}
					placeholder="What should never appear… (# for phrasebook)"
					{variables}
					{variableRolls}
					{onVariableDefChange}
					{onOpenVariableManager}
					{activeTriggerWords}
					on:change={(e) => handleSegmentUpdate('negative', segment.id, e.detail)}
					on:metadataChange={(e) => handleMetadataUpdate('negative', segment.id, e.detail)}
					on:remove={() => removeSegment('negative', segment.id)}
					on:duplicate={() => duplicateSegment('negative', segment.id)}
					on:toggleDisabled={() => toggleSegmentDisabled('negative', segment.id)}
					on:toggleBreak={() => toggleSegmentBreak('negative', segment.id)}
					on:moveUp={() => moveSegment('negative', segment.id, 'up')}
					on:moveDown={() => moveSegment('negative', segment.id, 'down')}
					on:saveAsSegment={() => {
						saveSegmentTarget = 'negative';
						saveSegmentId = segment.id;
					}}
					on:replaceFromSaved={() => openReplaceFromSaved('negative', segment.id)}
					on:drop={(e) => handleSegmentDrop('negative', e.detail.draggedId, e.detail.targetId, e.detail.position)}
				/>
			{/each}
		</div>

		<button type="button" class="add-row" on:click={() => addSegment('negative')}>
			<Icon name="plus" className="h-3.5 w-3.5" />
			<span>Add segment</span>
		</button>
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
		on:close={() => (saveSegmentId = null)}
		on:saved={() => (saveSegmentId = null)}
	/>
{/if}

<SavePromptModal
	isOpen={showSavePromptModal}
	segments={getList(savePromptTarget)}
	usageHint={savePromptUsageHint}
	on:close={() => (showSavePromptModal = false)}
	on:saved={() => (showSavePromptModal = false)}
/>

<style>
	.prompt-editor {
		container-type: inline-size;
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.section-header {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		padding: 0 0.125rem;
	}

	.negative-header {
		margin-top: 0.625rem;
	}

	.section-title {
		font-size: 0.8125rem;
		font-weight: 600;
		color: rgb(var(--fg));
	}

	.section-title.negative {
		color: rgb(var(--danger));
	}

	.section-count {
		font-size: 0.6875rem;
		color: rgb(var(--fg-subtle));
	}

	.header-spacer {
		flex: 1 1 auto;
	}

	.inline-warning {
		font-size: 0.6875rem;
		color: rgb(var(--warning));
	}

	.inline-note-warning {
		border-radius: 0.25rem;
		border: 1px solid rgb(var(--warning) / 0.25);
		background-color: rgb(var(--warning) / 0.1);
		padding: 0.125rem 0.375rem;
		font-size: 0.625rem;
		font-weight: 500;
		color: rgb(var(--warning));
	}

	.prompt-editor :global(.header-btn) {
		height: 1.75rem;
		border: 1px solid rgb(var(--line));
	}

	.count-badge {
		border-radius: 0.25rem;
		background-color: rgb(var(--signal) / 0.15);
		padding: 0.0625rem 0.3125rem;
		font-size: 0.625rem;
		color: rgb(var(--signal));
	}

	.header-icon-btn {
		display: inline-flex;
		width: 1.75rem;
		height: 1.75rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border: 1px solid rgb(var(--line));
		border-radius: 0.25rem;
		color: rgb(var(--fg-muted));
		transition: color 0.15s, background-color 0.15s;
	}

	.header-icon-btn:hover,
	.header-icon-btn.active {
		color: rgb(var(--fg));
		background-color: rgb(var(--surface-1));
	}

	.header-menu {
		position: absolute;
		top: calc(100% + 0.375rem);
		right: 0;
		z-index: 40;
		width: 12rem;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 0.625rem;
		padding: 0.25rem;
		background-color: rgb(var(--surface-1));
		box-shadow: var(--shadow-floating);
	}

	.header-menu button {
		display: flex;
		width: 100%;
		min-height: 2.25rem;
		align-items: center;
		gap: 0.5rem;
		border-radius: 0.375rem;
		padding: 0.5rem 0.625rem;
		font-size: 0.8125rem;
		color: rgb(var(--fg-muted));
		text-align: left;
	}

	.header-menu button:hover,
	.header-menu button:focus-visible {
		color: rgb(var(--fg));
		background-color: rgb(var(--surface-3));
		outline: none;
	}

	.segment-list {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.add-row {
		display: flex;
		height: 2.625rem;
		width: 100%;
		align-items: center;
		justify-content: center;
		gap: 0.4375rem;
		border: 1px dashed rgb(var(--line-strong));
		border-radius: 0.375rem;
		font-size: 0.75rem;
		font-weight: 500;
		color: rgb(var(--fg-muted));
		transition: color 0.15s, border-color 0.15s, background-color 0.15s;
	}

	.add-row:hover {
		border-color: rgb(var(--line-hover));
		background-color: rgb(var(--surface-1));
		color: rgb(var(--fg));
	}

	.resolved-divider {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		margin-top: 0.25rem;
	}

	.divider-rule {
		flex: 1 1 auto;
		height: 1px;
		background-color: rgb(var(--line));
	}

	.divider-label {
		font-size: 0.625rem;
		text-transform: uppercase;
		letter-spacing: 0.09em;
		color: rgb(var(--fg-subtle));
	}

	/* Deliberately the page tint rather than a panel fill: the resolved string is
	   output, not another editable surface, and it has to read as recessed
	   against the cards in both themes. */
	.resolved-panel {
		border: 1px solid rgb(var(--line-strong));
		border-radius: 0.375rem;
		background-color: rgb(var(--canvas));
		padding: 0.875rem 1rem 1rem;
		box-shadow: var(--shadow-raised);
	}

	.resolved-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5625rem;
		margin-bottom: 0.625rem;
	}

	.resolved-toggle {
		display: inline-flex;
		align-items: center;
		gap: 0.5625rem;
		color: rgb(var(--fg));
	}

	.resolved-title {
		font-size: 0.8125rem;
		font-weight: 600;
	}

	.resolved-stats {
		font-size: 0.625rem;
		color: rgb(var(--fg-subtle));
	}

	.resolved-copy {
		display: inline-flex;
		height: 1.625rem;
		flex-shrink: 0;
		align-items: center;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 0.25rem;
		padding: 0 0.5625rem;
		font-size: 0.6875rem;
		color: rgb(var(--fg-muted));
		transition: color 0.15s, background-color 0.15s;
	}

	.resolved-copy:hover {
		color: rgb(var(--fg));
		background-color: rgb(var(--surface-2));
	}

	.resolved-body {
		font-size: 0.8125rem;
		line-height: 1.85;
		color: rgb(var(--fg-muted));
		text-wrap: pretty;
		overflow-wrap: anywhere;
	}

	.resolved-value {
		color: rgb(var(--fg));
	}

	.resolved-emphasis {
		color: rgb(var(--signal));
	}

	.resolved-muted {
		color: rgb(var(--fg-subtle));
		text-decoration: line-through;
	}

	.resolved-break {
		border-radius: 0.25rem;
		background-color: rgb(var(--surface-3));
		padding: 0.0625rem 0.3125rem;
		font-size: 0.6875rem;
		letter-spacing: 0.08em;
		color: rgb(var(--fg-muted));
	}

	.resolved-empty {
		font-style: italic;
		color: rgb(var(--fg-subtle));
	}
</style>
