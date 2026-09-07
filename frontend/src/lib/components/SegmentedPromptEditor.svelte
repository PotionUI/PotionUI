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
	import SegmentComposerIconSprite from './SegmentComposerIconSprite.svelte';
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
	// The Video Director's shot stage (StageBeat.svelte) already gives this
	// editor its own caption/card — the mock's `.composer` shell (toolbar
	// header + resolved panel) would just be a second, redundant container
	// around the same segments. `embedded` drops that chrome down to the bare
	// segment rail (`.segment-list` + its add-segment row); the `.segment-composer`
	// scope class stays on the root either way, so chip/picker styling still applies.
	export let embedded = false;

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

<div class="prompt-editor segment-composer min-w-0" class:compact>
	<SegmentComposerIconSprite />

	{#snippet mainRail()}
		<div class="segment-lane">
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

		<button type="button" class="add-segment add-row" on:click={() => addSegment('main')}>
			<svg class="icon"><use href="#i-plus" /></svg>
			<span>Add segment</span>
		</button>
		</div>
	{/snippet}

	{#if embedded}
		{@render mainRail()}
	{:else}
		<section class="composer edge-inline" class:compact>
			<header class="composer-toolbar section-header">
				<strong class="composer-title section-title">{headerWord}</strong>
				<span class="composer-count section-count font-mono tabular-nums">{segmentCountLabel(segments.length)}</span>

				{#if !paired && isNegative && negativePromptUnavailable}
					<span class="inline-note-warning">Not used by this preset</span>
				{/if}

				<div class="toolbar-spacer"></div>

				{#if showLibraryActions}
					<Tooltip text="Insert a saved Segment" position="top">
						<button type="button" class="toolbar-button" on:click={() => openLibraryInsert('main')}>
							<svg class="icon"><use href="#i-library" /></svg>
							{#if !compact}<span>Library</span>{/if}
						</button>
					</Tooltip>
					<Tooltip text="Apply a Segment Template" position="top">
						<button type="button" class="toolbar-button" on:click={() => openTemplateApply('main')}>
							<svg class="icon"><use href="#i-template" /></svg>
							{#if !compact}<span>Template</span>{/if}
						</button>
					</Tooltip>
				{/if}

				{#if onOpenVariableManager}
					<Tooltip text="Manage prompt variables" position="top">
						<button type="button" class="toolbar-button" on:click={onOpenVariableManager}>
							<svg class="icon"><use href="#i-braces" /></svg>
							{#if !compact}<span>Variables</span>{/if}
							{#if variableCount > 0}
								<span class="badge font-mono tabular-nums">{variableCount}</span>
							{/if}
						</button>
					</Tooltip>
				{/if}

				{#if showLibraryActions || hasMainContent}
					<div class="relative" bind:this={mainMoreRoot}>
						<button
							type="button"
							class="toolbar-button more-button"
							class:active={mainMoreOpen}
							bind:this={mainMoreTrigger}
							aria-haspopup="menu"
							aria-expanded={mainMoreOpen}
							aria-label="More prompt actions"
							on:click={toggleMainMore}
						>
							<svg class="icon"><use href="#i-more" /></svg>
						</button>
						{#if mainMoreOpen}
							<div class="floating segment-menu header-menu" role="menu" aria-label="More prompt actions">
								<div class="menu-group">
									{#if showLibraryActions}
										<button type="button" role="menuitem" class="menu-item" on:click={() => runMainMore(() => openPromptApply('main'))}>
											<svg class="icon"><use href="#i-file" /></svg>
											<span>Apply Prompt</span>
										</button>
										<button type="button" role="menuitem" class="menu-item" on:click={() => runMainMore(() => openSavePrompt('main'))}>
											<svg class="icon"><use href="#i-save" /></svg>
											<span>Save as Prompt</span>
										</button>
									{/if}
									{#if hasMainContent}
										<button type="button" role="menuitem" class="menu-item" on:click={() => runMainMore(() => handleCopyPrompt('main'))}>
											<svg class="icon"><use href={copiedTarget === 'main' ? '#i-check' : '#i-copy'} /></svg>
											<span>{copiedTarget === 'main' ? 'Copied' : 'Copy prompt'}</span>
										</button>
									{/if}
								</div>
							</div>
						{/if}
					</div>
				{/if}
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
							<svg class="icon"><use href="#i-chevron-down" /></svg>
							<span class="resolved-title">What the model receives</span>
						</button>
						<span class="resolved-stats font-mono tabular-nums">
							{resolvedStats.chars} chars · {resolvedStats.breaks} {resolvedStats.breaks === 1 ? 'break' : 'breaks'}
						</span>
						{#if hasMainContent}
							<button type="button" class="resolved-copy small-button" on:click={() => handleCopyPrompt('main')}>
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

			<button type="button" class="add-segment add-row" on:click={() => addSegment('negative')}>
				<svg class="icon"><use href="#i-plus" /></svg>
				<span>Add segment</span>
			</button>
			</div>
		{/snippet}

		{#if embedded}
			{@render negativeRail()}
		{:else}
			<section class="composer edge-inline negative-composer" class:compact>
				<header class="composer-toolbar section-header negative-header">
					<strong class="composer-title negative section-title negative">Negative</strong>
					<span class="composer-count section-count font-mono tabular-nums">{segmentCountLabel(negativeCount)}</span>

					{#if negativePromptUnavailable}
						<span class="inline-note-warning">Not used by this preset</span>
					{:else if negativeInert}
						<span class="inline-warning">Not applied at current guidance</span>
					{/if}

					<div class="toolbar-spacer"></div>

					{#if showLibraryActions}
						<Tooltip text="Insert a saved Segment" position="top">
							<button type="button" class="toolbar-button" on:click={() => openLibraryInsert('negative')}>
								<svg class="icon"><use href="#i-library" /></svg>
								{#if !compact}<span>Library</span>{/if}
							</button>
						</Tooltip>
						<Tooltip text="Apply a Segment Template" position="top">
							<button type="button" class="toolbar-button" on:click={() => openTemplateApply('negative')}>
								<svg class="icon"><use href="#i-template" /></svg>
								{#if !compact}<span>Template</span>{/if}
							</button>
						</Tooltip>
					{/if}

					{#if showLibraryActions || hasNegativeContent}
						<div class="relative" bind:this={negativeMoreRoot}>
							<button
								type="button"
								class="toolbar-button more-button"
								class:active={negativeMoreOpen}
								bind:this={negativeMoreTrigger}
								aria-haspopup="menu"
								aria-expanded={negativeMoreOpen}
								aria-label="More negative prompt actions"
								on:click={toggleNegativeMore}
							>
								<svg class="icon"><use href="#i-more" /></svg>
							</button>
							{#if negativeMoreOpen}
								<div class="floating segment-menu header-menu" role="menu" aria-label="More negative prompt actions">
									<div class="menu-group">
										{#if showLibraryActions}
											<button type="button" role="menuitem" class="menu-item" on:click={() => runNegativeMore(() => openPromptApply('negative'))}>
												<svg class="icon"><use href="#i-file" /></svg>
												<span>Apply Prompt</span>
											</button>
											<button type="button" role="menuitem" class="menu-item" on:click={() => runNegativeMore(() => openSavePrompt('negative'))}>
												<svg class="icon"><use href="#i-save" /></svg>
												<span>Save as Prompt</span>
											</button>
										{/if}
										{#if hasNegativeContent}
											<button type="button" role="menuitem" class="menu-item" on:click={() => runNegativeMore(() => handleCopyPrompt('negative'))}>
												<svg class="icon"><use href={copiedTarget === 'negative' ? '#i-check' : '#i-copy'} /></svg>
												<span>{copiedTarget === 'negative' ? 'Copied' : 'Copy prompt'}</span>
											</button>
										{/if}
									</div>
								</div>
							{/if}
						</div>
					{/if}
				</header>

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
	/* Chrome, toolbar, segment-list, add-row and resolved-panel visuals all
	   come from the ported `.composer`/`.composer-toolbar`/`.toolbar-button`/
	   `.segment-list`/`.add-segment`/`.resolved*` rules in segment-composer.css
	   (imported globally, scoped under this root's own `.segment-composer`
	   class). This block is only the handful of layout/state bits the mock
	   doesn't need to say anything about. */

	.prompt-editor {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.negative-composer {
		margin-top: 0.125rem;
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
</style>
