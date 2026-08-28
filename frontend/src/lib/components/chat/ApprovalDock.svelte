<script lang="ts">
	/**
	 * Docked approval queue, rendered above the composer whenever a tool
	 * execution somewhere in the conversation is waiting on the user. Replaces
	 * the old inline ChatToolApproval card: approvals are an action surface,
	 * not transcript content, so they live here instead of interleaved with
	 * messages — the transcript keeps only a compact status chip
	 * (see ChatBehaviorTrace / ChatToolChip).
	 *
	 * Default posture is a compact summary row (tool chip, one-line summary,
	 * key-setting chips); "Review full details" expands it in place to the
	 * full typed renderer for the preview's `kind`, and a header button
	 * promotes that same content into a centered sheet for a taller read.
	 * Legacy previews (no `kind`) keep their existing changeGroups/diff/items
	 * renderers, just inside the same compact/expand shell.
	 */
	import { slide } from 'svelte/transition';
	import { logger } from '$lib/utils/logger';
	import { processMarkdown } from '$lib/utils/markdown';
	import type { UnifiedChatMessageData, ToolExecution } from '$lib/types/chat';
	import { chatModes } from '$lib/stores/chatModes';
	import { deriveApprovalQueue } from '$lib/chat/approvalQueue';
	import { resolveApproval, type ApprovalResolution } from '$lib/chat/approvalResolve';
	import {
		buildApprovalDiff,
		buildArgumentTree,
		buildDirectorChangeGroups,
		deriveCompactSummary
	} from '$lib/chat/approvalPreview';
	import { composeQuestionAnswer, deriveQuestionQueue, dismissedQuestions } from '$lib/chat/questionQueue';
	import { Badge } from '$lib/components/ui';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ApprovalArgTree from './approval/ApprovalArgTree.svelte';

	export let messages: UnifiedChatMessageData[] = [];
	export let sessionId: string = '';
	/** Bubbles a resolved approval (with any continuation message) up to the conversation owner. */
	export let onResolved:
		| ((data: { messageId: string } & ApprovalResolution) => void)
		| undefined = undefined;
	/** Sends a docked question's answer as a normal user turn (see UnifiedAIChat's sendMessage). */
	export let onAnswerQuestion: ((text: string) => void | Promise<void>) | undefined = undefined;

	const ITEM_PREVIEW_COUNT = 5;
	const MAX_PIPS = 6;
	const CLAMP_TEXT_LENGTH = 220;

	let itemsExpanded = false;
	let detailExpanded = false;
	let sheetOpen = false;
	let expandedBlocks = new Set<number>();
	let working: 'one' | 'all' | null = null;
	let error: string | null = null;

	$: queue = deriveApprovalQueue(messages);
	$: current = queue[0] ?? null;
	$: nextEntry = queue[1] ?? null;
	// A fresh current entry resets any stale expand/error state from the last one.
	$: current, ((itemsExpanded = false), (detailExpanded = false), (sheetOpen = false), (expandedBlocks = new Set()), (error = null));

	// Questions rank below approvals — approvals gate side effects, questions
	// are optional — so they only ever surface once the approval queue drains.
	$: questionQueue = deriveQuestionQueue(messages, $dismissedQuestions);
	$: currentQuestion = current ? null : (questionQueue[0] ?? null);

	const OTHER_OPTION = '\x00OTHER\x00';
	let selectedOption: string | null = null;
	let otherText = '';
	// A fresh current question resets any stale selection from the last one.
	$: currentQuestion, ((selectedOption = null), (otherText = ''));

	$: answerText =
		selectedOption && selectedOption !== OTHER_OPTION ? selectedOption : otherText.trim();
	$: canAnswer = !!currentQuestion && answerText.length > 0;

	function selectOption(option: string) {
		selectedOption = selectedOption === option ? null : option;
	}

	async function answerQuestion() {
		if (!currentQuestion || !canAnswer) return;
		const quoted = composeQuestionAnswer(currentQuestion.text, answerText);
		dismissedQuestions.dismiss(currentQuestion.messageId, currentQuestion.index);
		await onAnswerQuestion?.(quoted);
	}

	function skipQuestion() {
		if (!currentQuestion) return;
		dismissedQuestions.dismiss(currentQuestion.messageId, currentQuestion.index);
	}

	function skipAllQuestions() {
		for (const entry of questionQueue) {
			dismissedQuestions.dismiss(entry.messageId, entry.index);
		}
	}

	function labelFor(name: string): string {
		const info = $chatModes.toolsCatalog.find((t) => t.name === name);
		return info?.label || name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
	}

	function formatTime(ts: number | undefined): string {
		if (!ts) return '';
		return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
	}

	function toggleBlock(index: number) {
		if (expandedBlocks.has(index)) expandedBlocks.delete(index);
		else expandedBlocks.add(index);
		expandedBlocks = expandedBlocks;
	}

	function isChangedField(field: { old?: string }): boolean {
		return field.old !== undefined && field.old !== null;
	}

	const changeKindVariant: Record<'add' | 'remove' | 'update', 'success' | 'danger' | 'info'> = {
		add: 'success',
		remove: 'danger',
		update: 'info'
	};

	function badgeVariantForOp(op: string | undefined): 'success' | 'danger' | 'info' | 'neutral' {
		if (op === 'add' || op === 'remove' || op === 'update') return changeKindVariant[op];
		return 'neutral';
	}

	$: toolLabel = current ? labelFor(current.execution.tool_name) : '';
	$: previewData = current?.execution.preview ?? null;
	$: kind = previewData?.kind ?? null;
	$: hasTypedContent =
		kind === 'generation'
			? !!(previewData?.fields?.length || previewData?.text_blocks?.length)
			: kind === 'timeline'
				? !!previewData?.rows?.length
				: kind === 'text_edit'
					? !!previewData?.text_blocks?.length
					: false;

	$: changeGroups = !hasTypedContent && current ? buildDirectorChangeGroups(previewData) : null;
	$: shownChangeGroups = changeGroups
		? itemsExpanded
			? changeGroups
			: changeGroups.slice(0, ITEM_PREVIEW_COUNT)
		: [];
	$: hiddenChangeGroupCount = (changeGroups?.length ?? 0) - shownChangeGroups.length;
	$: diff = !hasTypedContent && !changeGroups && current ? buildApprovalDiff(current.execution) : null;
	$: legacyPreview = !hasTypedContent && !changeGroups && !diff ? previewData : null;
	$: legacyItems = legacyPreview?.items ?? [];
	$: shownLegacyItems = itemsExpanded ? legacyItems : legacyItems.slice(0, ITEM_PREVIEW_COUNT);
	$: hiddenLegacyItemCount = legacyItems.length - shownLegacyItems.length;
	$: isFallback = !!current && !hasTypedContent && !changeGroups && !diff && !legacyPreview;
	$: argTree = isFallback && current ? buildArgumentTree(current.execution) : [];

	$: compactSummary = current ? deriveCompactSummary(current.execution, toolLabel, previewData) : '';

	async function approve() {
		await resolveOne(true);
	}

	async function reject() {
		await resolveOne(false);
	}

	async function resolveOne(approved: boolean) {
		if (!current || working) return;
		working = 'one';
		error = null;
		try {
			const resolution = await resolveApproval(
				sessionId,
				current.messageId,
				current.index,
				current.execution,
				approved
			);
			onResolved?.({ messageId: current.messageId, ...resolution });
		} catch (err: any) {
			logger.error('Tool approval failed:', err);
			error = err?.message || 'Failed to resolve approval';
		} finally {
			working = null;
		}
	}

	async function approveAll() {
		if (working) return;
		working = 'all';
		error = null;
		// Snapshot: each resolution patches the owning message, which re-derives
		// `queue` from the `messages` prop asynchronously — iterate the entries
		// captured at click time instead of a queue that shifts under us.
		const snapshot = queue;
		for (const entry of snapshot) {
			try {
				const resolution = await resolveApproval(
					sessionId,
					entry.messageId,
					entry.index,
					entry.execution,
					true
				);
				onResolved?.({ messageId: entry.messageId, ...resolution });
			} catch (err: any) {
				logger.error('Tool approval failed:', err);
				error = err?.message || 'Failed to resolve approval';
				break;
			}
		}
		working = null;
	}
</script>

{#if current}
	{#snippet detailContent()}
		{#if kind === 'generation'}
			{#each previewData?.text_blocks ?? [] as block, i}
				<div class="mb-3">
					<div class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1">{block.label}</div>
					{#if block.old_text !== undefined && block.old_text !== null}
						<div class="rounded-lg border border-line overflow-hidden">
							<div class="px-2.5 py-2 bg-canvas text-xs leading-relaxed text-fg-disabled line-through">{block.old_text}</div>
							<div class="flex items-center gap-1.5 px-2.5 py-1 bg-surface-2 border-y border-line font-mono text-2xs uppercase tracking-[0.05em] text-fg-subtle">
								changed to
							</div>
							<div class="px-2.5 py-2 bg-signal/5 text-xs leading-relaxed text-fg">{block.text}</div>
						</div>
					{:else}
						<div class="text-xs leading-relaxed text-fg {block.text.length > CLAMP_TEXT_LENGTH && !expandedBlocks.has(i) ? 'line-clamp-3' : ''}">{block.text}</div>
						{#if block.text.length > CLAMP_TEXT_LENGTH}
							<button
								type="button"
								class="mt-1 text-2xs font-semibold text-signal hover:text-signal/80 transition-colors"
								on:click={() => toggleBlock(i)}
							>
								{expandedBlocks.has(i) ? 'Show less' : 'Show full prompt'}
							</button>
						{/if}
					{/if}
				</div>
			{/each}
			{#if previewData?.fields?.length}
				<div class="grid grid-cols-2 gap-x-4 gap-y-2.5">
					{#each previewData.fields as field}
						<div class="min-w-0 {isChangedField(field) ? 'border-l-2 border-signal pl-2 -ml-2.5' : ''}">
							<div class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-0.5">{field.label}</div>
							<div class="font-mono text-xs {isChangedField(field) ? 'text-signal' : 'text-fg'} {field.mono === false ? '' : 'tabular-nums'}">
								{#if isChangedField(field)}
									<span class="text-fg-disabled line-through mr-1">{field.old}</span><span class="text-fg-subtle mr-1">→</span>
								{/if}
								{field.value}
							</div>
						</div>
					{/each}
				</div>
			{/if}
		{:else if kind === 'text_edit'}
			{#each previewData?.text_blocks ?? [] as block, i}
				<div class="mb-3">
					<div class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle mb-1">{block.label}</div>
					{#if block.old_text !== undefined && block.old_text !== null}
						<div class="rounded-lg border border-line overflow-hidden">
							<div class="px-2.5 py-2 bg-canvas text-xs leading-relaxed text-fg-disabled line-through">{block.old_text}</div>
							<div class="flex items-center gap-1.5 px-2.5 py-1 bg-surface-2 border-y border-line font-mono text-2xs uppercase tracking-[0.05em] text-fg-subtle">
								changed to
							</div>
							<div class="px-2.5 py-2 bg-signal/5 text-xs leading-relaxed text-fg">{block.text}</div>
						</div>
					{:else}
						<div class="text-xs leading-relaxed text-fg {block.text.length > CLAMP_TEXT_LENGTH && !expandedBlocks.has(i) ? 'line-clamp-3' : ''}">{block.text}</div>
						{#if block.text.length > CLAMP_TEXT_LENGTH}
							<button
								type="button"
								class="mt-1 text-2xs font-semibold text-signal hover:text-signal/80 transition-colors"
								on:click={() => toggleBlock(i)}
							>
								{expandedBlocks.has(i) ? 'Show less' : 'Show full text'}
							</button>
						{/if}
					{/if}
				</div>
			{/each}
		{:else if kind === 'timeline'}
			<div>
				{#each previewData?.rows ?? [] as row}
					<div class="flex gap-2.5 py-2 border-b border-line last:border-b-0">
						<span class="flex-shrink-0 h-fit rounded border border-line-strong bg-surface-2 px-1.5 py-0.5 font-mono text-2xs tabular-nums text-fg-muted">{row.range}</span>
						<div class="min-w-0 flex-1">
							{#if row.op}
								<div class="mb-0.5"><Badge variant={badgeVariantForOp(row.op)} size="sm">{row.op}</Badge></div>
							{/if}
							<div class="text-xs leading-relaxed text-fg">{row.text}</div>
						</div>
					</div>
				{/each}
			</div>
		{:else if changeGroups}
			<div class="space-y-1.5">
				{#each shownChangeGroups as group}
					<div class="rounded border border-line bg-canvas p-2">
						<div class="flex items-center gap-2 flex-wrap">
							<Badge variant={changeKindVariant[group.kind]} size="sm">{group.kind}</Badge>
							<span class="text-xs text-fg-muted break-words">{group.summary}</span>
						</div>
						{#if group.rows.length}
							<div class="mt-1 font-mono text-xs space-y-1">
								{#each group.rows as row}
									<div class="flex items-start gap-2 flex-wrap">
										<span class="text-fg-subtle flex-shrink-0">{row.field}</span>
										<span class="text-fg-disabled line-through break-all">{row.oldValue}</span>
										<svg class="w-3 h-3 mt-0.5 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
										</svg>
										<span class="text-fg break-all">{row.newValue}</span>
									</div>
								{/each}
							</div>
						{/if}
					</div>
				{/each}
				{#if hiddenChangeGroupCount > 0 || itemsExpanded}
					<button
						type="button"
						class="text-xs text-fg-subtle hover:text-fg-muted transition-colors"
						on:click={() => (itemsExpanded = !itemsExpanded)}
					>
						{itemsExpanded ? 'Show less' : `+${hiddenChangeGroupCount} more`}
					</button>
				{/if}
			</div>
		{:else if diff}
			<div class="font-mono text-xs bg-canvas border border-line rounded p-2 space-y-1">
				{#each diff as row}
					<div class="flex items-center gap-2 flex-wrap">
						<span class="text-fg-muted">{row.field}</span>
						<span class="text-fg-disabled line-through tabular-nums">{row.oldValue}</span>
						<svg class="w-3 h-3 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7l5 5m0 0l-5 5m5-5H6" />
						</svg>
						<span class="text-fg tabular-nums">{row.newValue}</span>
						{#if row.reason}
							<span class="text-fg-subtle text-2xs basis-full pl-0">{row.reason}</span>
						{/if}
					</div>
				{/each}
			</div>
		{:else if legacyPreview}
			<div class="text-sm text-fg leading-snug">
				<span class="font-semibold">{legacyPreview.action}</span>{#if legacyPreview.target}<span class="text-fg-muted"> {legacyPreview.target}</span>{/if}
			</div>
			{#if legacyItems.length}
				<div class="mt-1.5 flex flex-wrap gap-1.5">
					{#each shownLegacyItems as item}
						<span class="inline-flex items-center rounded border border-line bg-surface-2 px-1.5 py-0.5 text-xs text-fg-muted">{item}</span>
					{/each}
					{#if hiddenLegacyItemCount > 0 || itemsExpanded}
						<button
							type="button"
							class="inline-flex items-center rounded px-1.5 py-0.5 text-xs text-fg-subtle hover:text-fg-muted transition-colors"
							on:click={() => (itemsExpanded = !itemsExpanded)}
						>
							{itemsExpanded ? 'Show less' : `+${hiddenLegacyItemCount} more`}
						</button>
					{/if}
				</div>
			{/if}
			{#if legacyPreview.note}
				<div class="mt-1.5 text-xs text-fg-subtle">{legacyPreview.note}</div>
			{/if}
		{:else}
			<ApprovalArgTree nodes={argTree} />
		{/if}
	{/snippet}

	{#snippet actionsRow()}
		{#if working}
			<span class="text-xs text-fg-subtle flex items-center gap-1.5 ml-auto">
				<span
					class="w-3 h-3 rounded-full border-2 border-line-strong animate-spin inline-block"
					style="border-top-color: rgb(var(--accent));"
				></span>
				Working…
			</span>
		{:else}
			<span class="font-mono text-2xs text-fg-subtle truncate">
				{#if nextEntry}next: {labelFor(nextEntry.execution.tool_name)}{/if}
			</span>
			<div class="flex items-center gap-2 flex-shrink-0">
				{#if queue.length > 1}
					<button
						type="button"
						class="px-3 py-1.5 text-xs font-medium text-fg-muted border border-line-strong rounded hover:bg-surface-2 transition-colors"
						on:click={approveAll}
					>
						Approve all
					</button>
				{/if}
				<button
					type="button"
					class="px-3 py-1.5 text-xs font-medium text-danger border border-danger/45 bg-transparent rounded hover:bg-danger/10 transition-colors"
					on:click={reject}
				>
					Reject
				</button>
				<button
					type="button"
					class="px-3.5 py-1.5 text-xs font-medium text-accent-contrast bg-accent rounded hover:bg-accent-hover transition-colors"
					on:click={approve}
				>
					Approve
				</button>
			</div>
		{/if}
	{/snippet}

	<div class="flex-shrink-0 border-t border-line-strong bg-surface-1 shadow-raised" transition:slide={{ duration: 150 }}>
		<div class="p-2.5 pb-0">
			<div class="flex items-center gap-2">
				<span
					class="flex items-center justify-center w-[18px] h-[18px] rounded bg-warning/10 border border-warning/40 flex-shrink-0 {working ? 'opacity-50' : ''}"
					aria-hidden="true"
				>
					<span class="w-1.5 h-1.5 rounded-full bg-warning {working ? '' : 'motion-safe:animate-pulse'}"></span>
				</span>
				<span class="text-sm font-semibold {working ? 'text-fg-muted' : 'text-fg'} truncate">{toolLabel}</span>
				{#if detailExpanded}
					<button
						type="button"
						class="w-6 h-6 rounded border border-line-strong flex items-center justify-center text-fg-subtle hover:text-fg-muted hover:border-line-hover transition-colors flex-shrink-0"
						title="Expand to full view"
						aria-label="Expand to full view"
						on:click={() => (sheetOpen = true)}
					>
						<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3H3v6M15 21h6v-6M21 3l-7 7M3 21l7-7" />
						</svg>
					</button>
				{/if}
				<div class="ml-auto flex items-center gap-2 flex-shrink-0">
					{#if queue.length > 1}
						{#if queue.length <= MAX_PIPS}
							<div class="flex items-center gap-1" aria-hidden="true">
								{#each queue as _, i (i)}
									<span class="w-[5px] h-[5px] rounded-full {i === 0 ? 'bg-fg' : 'bg-line-strong'}"></span>
								{/each}
							</div>
						{/if}
						<span class="font-mono text-2xs uppercase tracking-[0.05em] text-fg-subtle tabular-nums">
							1 of {queue.length}
						</span>
					{/if}
				</div>
			</div>
			<div class="mt-0.5 font-mono text-2xs text-fg-subtle">
				from reply{#if current.messageTimestamp} · {formatTime(current.messageTimestamp)}{/if}{#if isFallback} · no typed preview — showing raw arguments{/if}
			</div>
			<div class="mt-2.5 border-t border-line"></div>
		</div>

		<div class="px-2.5 pb-2.5 {working ? 'opacity-45 pointer-events-none' : ''}">
			{#if !detailExpanded}
				<div class="pt-2.5">
					<div class="text-sm text-fg leading-snug line-clamp-2">{compactSummary}</div>
					{#if previewData?.fields?.length}
						<div class="mt-2 flex flex-wrap gap-1.5">
							{#each previewData.fields as field}
								<span class="inline-flex items-center gap-1 rounded border border-line-strong bg-surface-2 px-1.5 py-0.5 font-mono text-2xs text-fg-muted">
									{#if isChangedField(field)}<span class="w-1.5 h-1.5 rounded-full bg-signal flex-shrink-0" aria-hidden="true"></span>{/if}
									{field.label.toLowerCase()} {field.value}
								</span>
							{/each}
						</div>
					{/if}
					<button
						type="button"
						class="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-signal hover:text-signal/80 transition-colors"
						on:click={() => (detailExpanded = true)}
					>
						Review full details
						<svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M9 6l6 6-6 6" />
						</svg>
					</button>
				</div>
			{:else}
				<div class="pt-2.5">
					<button
						type="button"
						class="mb-1.5 text-xs font-semibold text-fg-subtle hover:text-fg-muted transition-colors"
						on:click={() => (detailExpanded = false)}
					>
						Show summary
					</button>
					<div class="relative">
						<div class="max-h-[260px] overflow-y-auto pr-1">
							{@render detailContent()}
						</div>
						<div class="pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-surface-1 to-transparent" aria-hidden="true"></div>
					</div>
				</div>
			{/if}

			{#if error}
				<div class="mt-1.5 text-xs text-danger">{error}</div>
			{/if}

			<div class="mt-2.5 flex items-center justify-between gap-2">
				{@render actionsRow()}
			</div>
		</div>
	</div>

	<BaseModal
		isOpen={sheetOpen}
		title={toolLabel}
		size="md"
		on:close={() => (sheetOpen = false)}
	>
		<svelte:fragment slot="headerIcon">
			<span class="flex items-center justify-center w-[18px] h-[18px] rounded bg-warning/10 border border-warning/40 flex-shrink-0" aria-hidden="true">
				<span class="w-1.5 h-1.5 rounded-full bg-warning motion-safe:animate-pulse"></span>
			</span>
		</svelte:fragment>
		<svelte:fragment slot="header">
			{#if queue.length > 1}
				<span class="font-mono text-2xs uppercase tracking-[0.05em] text-fg-subtle tabular-nums">1 of {queue.length}</span>
			{/if}
		</svelte:fragment>
		<div class="p-4">
			{@render detailContent()}
		</div>
		<svelte:fragment slot="footer">
			<div class="p-2.5 flex items-center justify-between gap-2">
				{@render actionsRow()}
			</div>
		</svelte:fragment>
	</BaseModal>
{:else if currentQuestion}
	<div class="flex-shrink-0 border-t border-line-strong/60 bg-surface-1 p-2.5" transition:slide={{ duration: 150 }}>
		<div class="flex items-center gap-2">
			<span class="w-1.5 h-1.5 rounded-full bg-signal flex-shrink-0" aria-hidden="true"></span>
			<span class="font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-subtle tabular-nums">
				Question {currentQuestion.index + 1} of {currentQuestion.total}
			</span>
		</div>
		<div class="mt-0.5 font-mono text-2xs text-fg-subtle">
			from reply{#if currentQuestion.messageTimestamp} · {formatTime(currentQuestion.messageTimestamp)}{/if}
		</div>

		<div class="mt-2 text-sm text-fg leading-snug">{@html processMarkdown(currentQuestion.text)}</div>

		{#if currentQuestion.options.length}
			<div class="mt-2 flex flex-wrap gap-1.5">
				{#each currentQuestion.options as option}
					<button
						type="button"
						class="px-2.5 py-1 text-xs font-medium rounded border transition-colors {selectedOption === option
							? 'border-signal bg-signal/10 text-signal'
							: 'border-line text-fg-muted hover:border-line-hover'}"
						aria-pressed={selectedOption === option}
						on:click={() => selectOption(option)}
					>
						{option}
					</button>
				{/each}
				<button
					type="button"
					class="px-2.5 py-1 text-xs font-medium rounded border transition-colors {selectedOption === OTHER_OPTION
						? 'border-signal bg-signal/10 text-signal'
						: 'border-line text-fg-muted hover:border-line-hover'}"
					aria-pressed={selectedOption === OTHER_OPTION}
					on:click={() => selectOption(OTHER_OPTION)}
				>
					Other…
				</button>
			</div>
		{/if}

		{#if !currentQuestion.options.length || selectedOption === OTHER_OPTION}
			<input
				type="text"
				bind:value={otherText}
				placeholder="Type your answer…"
				class="mt-2 w-full text-sm bg-canvas border border-line rounded px-2.5 py-1.5 text-fg placeholder-fg-subtle focus:outline-none focus:border-line-strong"
				on:keydown={(e) => e.key === 'Enter' && answerQuestion()}
			/>
		{/if}

		<div class="mt-2.5 flex items-center justify-between gap-2">
			<span class="font-mono text-2xs text-fg-subtle truncate">
				{#if questionQueue.length > 1}{questionQueue.length - 1} more queued{/if}
			</span>
			<div class="flex items-center gap-2 flex-shrink-0">
				{#if questionQueue.length > 1}
					<button
						type="button"
						class="px-3 py-1.5 text-xs font-medium text-fg-muted border border-line-strong rounded hover:bg-surface-2 transition-colors"
						on:click={skipAllQuestions}
					>
						Skip all
					</button>
				{/if}
				<button
					type="button"
					class="px-3 py-1.5 text-xs font-medium text-fg-muted border border-line-strong rounded hover:bg-surface-2 transition-colors"
					on:click={skipQuestion}
				>
					Skip
				</button>
				<button
					type="button"
					disabled={!canAnswer}
					class="px-3.5 py-1.5 text-xs font-medium rounded transition-colors {canAnswer
						? 'text-accent-contrast bg-accent hover:bg-accent-hover'
						: 'text-fg-disabled bg-surface-2 cursor-not-allowed'}"
					on:click={answerQuestion}
				>
					Answer
				</button>
			</div>
		</div>
	</div>
{/if}
