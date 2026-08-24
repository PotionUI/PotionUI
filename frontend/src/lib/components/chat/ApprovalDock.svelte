<script lang="ts">
	/**
	 * Docked approval queue, rendered above the composer whenever a tool
	 * execution somewhere in the conversation is waiting on the user. Replaces
	 * the old inline ChatToolApproval card: approvals are an action surface,
	 * not transcript content, so they live here instead of interleaved with
	 * messages — the transcript keeps only a compact status chip
	 * (see ChatBehaviorTrace / ChatToolChip).
	 */
	import { slide } from 'svelte/transition';
	import { logger } from '$lib/utils/logger';
	import { processMarkdown } from '$lib/utils/markdown';
	import type { UnifiedChatMessageData, ToolExecution } from '$lib/types/chat';
	import { chatModes } from '$lib/stores/chatModes';
	import { deriveApprovalQueue } from '$lib/chat/approvalQueue';
	import { resolveApproval, type ApprovalResolution } from '$lib/chat/approvalResolve';
	import { buildApprovalDiff, buildDirectorChangeGroups, humanizeApprovalArguments } from '$lib/chat/approvalPreview';
	import { composeQuestionAnswer, deriveQuestionQueue, dismissedQuestions } from '$lib/chat/questionQueue';
	import { Badge } from '$lib/components/ui';

	export let messages: UnifiedChatMessageData[] = [];
	export let sessionId: string = '';
	/** Bubbles a resolved approval (with any continuation message) up to the conversation owner. */
	export let onResolved:
		| ((data: { messageId: string } & ApprovalResolution) => void)
		| undefined = undefined;
	/** Sends a docked question's answer as a normal user turn (see UnifiedAIChat's sendMessage). */
	export let onAnswerQuestion: ((text: string) => void | Promise<void>) | undefined = undefined;

	const ITEM_PREVIEW_COUNT = 5;
	let itemsExpanded = false;
	let working: 'one' | 'all' | null = null;
	let error: string | null = null;

	$: queue = deriveApprovalQueue(messages);
	$: current = queue[0] ?? null;
	$: nextEntry = queue[1] ?? null;
	// A fresh current entry resets any stale expand/error state from the last one.
	$: current, ((itemsExpanded = false), (error = null));

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

	$: changeGroups = current ? buildDirectorChangeGroups(current.execution.preview) : null;
	$: shownChangeGroups = changeGroups
		? itemsExpanded
			? changeGroups
			: changeGroups.slice(0, ITEM_PREVIEW_COUNT)
		: [];
	$: hiddenChangeGroupCount = (changeGroups?.length ?? 0) - shownChangeGroups.length;
	$: diff = !changeGroups && current ? buildApprovalDiff(current.execution) : null;
	$: preview = !changeGroups && !diff ? (current?.execution.preview ?? null) : null;
	$: items = preview?.items ?? [];
	$: shownItems = itemsExpanded ? items : items.slice(0, ITEM_PREVIEW_COUNT);
	$: hiddenCount = items.length - shownItems.length;
	$: fallbackSummary =
		current && !changeGroups && !diff && !preview ? humanizeApprovalArguments(current.execution) : '';

	const changeKindVariant: Record<'add' | 'remove' | 'update', 'success' | 'danger' | 'info'> = {
		add: 'success',
		remove: 'danger',
		update: 'info'
	};

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
	<div
		class="flex-shrink-0 border-t border-warning/35 bg-warning/[0.06] p-2.5"
		transition:slide={{ duration: 150 }}
	>
		<div class="flex items-center gap-2">
			<span
				class="w-1.5 h-1.5 rounded-full bg-warning motion-safe:animate-pulse flex-shrink-0"
				aria-hidden="true"
			></span>
			<span class="text-sm font-semibold text-fg truncate">{labelFor(current.execution.tool_name)}</span>
			{#if queue.length > 1}
				<span class="ml-auto font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle tabular-nums flex-shrink-0">
					Approval 1 of {queue.length}
				</span>
			{/if}
		</div>
		<div class="mt-0.5 font-mono text-2xs text-fg-subtle">
			from reply{#if current.messageTimestamp} · {formatTime(current.messageTimestamp)}{/if}
		</div>

		<div class="mt-2 min-w-0">
			{#if changeGroups}
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
			{:else if preview}
				<div class="text-sm text-fg leading-snug">
					<span class="font-semibold">{preview.action}</span>{#if preview.target}<span class="text-fg-muted"> {preview.target}</span>{/if}
				</div>
				{#if items.length}
					<div class="mt-1.5 flex flex-wrap gap-1.5">
						{#each shownItems as item}
							<span class="inline-flex items-center rounded border border-line bg-surface-2 px-1.5 py-0.5 text-xs text-fg-muted">{item}</span>
						{/each}
						{#if hiddenCount > 0 || itemsExpanded}
							<button
								type="button"
								class="inline-flex items-center rounded px-1.5 py-0.5 text-xs text-fg-subtle hover:text-fg-muted transition-colors"
								on:click={() => (itemsExpanded = !itemsExpanded)}
							>
								{itemsExpanded ? 'Show less' : `+${hiddenCount} more`}
							</button>
						{/if}
					</div>
				{/if}
				{#if preview.note}
					<div class="mt-1.5 text-xs text-fg-subtle">{preview.note}</div>
				{/if}
			{:else}
				<div class="text-sm text-fg leading-snug">{fallbackSummary}</div>
			{/if}
		</div>

		{#if error}
			<div class="mt-1.5 text-xs text-danger">{error}</div>
		{/if}

		<div class="mt-2.5 flex items-center justify-between gap-2">
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
		</div>
	</div>
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
