<script lang="ts">
	import { slide } from 'svelte/transition';
	import {
		processMarkdown,
		processMarkdownWithActions,
		injectToolCallChips,
		truncateAtReplyContractMarker,
		type ToolAction
	} from '$lib/utils/markdown';
	import type { ToolExecution, ResourceRef, TraceStep, ReplyContract } from '$lib/types/chat';
	import { splitResourceTokens } from '$lib/utils/resourceTokens';
	import { buildVariableChipTooltips } from '$lib/utils/variableSnapshot';
	import type { VariablesMap, VariableRoll } from '$lib/utils/variableDefs';
	import ChatBehaviorTrace from '$lib/components/chat/ChatBehaviorTrace.svelte';
	import { appliedSegmentActions, isAppliedSegmentAction } from '$lib/stores/appliedSegmentActions';

	export let role: 'user' | 'assistant' | 'system';
	export let content: string;
	export let timestamp: number | undefined = undefined;
	export let imageUrl: string | undefined = undefined;
	export let compact: boolean = false;
	export let isStreaming: boolean = false;
	export let toolExecutions: ToolExecution[] = [];
	export let traceSteps: TraceStep[] = [];
	export let sources: Array<{
		source_type: string;
		title: string;
		subtitle?: string;
		description?: string;
		url?: string;
		icon?: string;
		metadata?: Record<string, any>;
	}> = [];
	export let sessionId: string = '';
	export let messageId: string = '';
	export let metadata: Record<string, any> | undefined = undefined;
	export let parsedContent: { reply_contract?: ReplyContract } | undefined = undefined;
	// The active tab's prompt variables + last rolls. `${name}` occurrences in
	// message text render as read-only chips for names known here.
	export let variables: VariablesMap | undefined = undefined;
	export let variableRolls: Record<string, VariableRoll> | undefined = undefined;
	export let onApplyAction:
		| ((
				action: { type: string; segmentIndex: number; segmentId: string; content: string },
				actionIndex: number
		  ) => void)
		| undefined = undefined;
	// Optional tooltip clarifying where "Apply" writes to when that isn't the
	// obvious flat segment list — e.g. Video Director's persistent Direction prompt.
	export let applyActionHint: string | undefined = undefined;
	export let onPromptFeedback:
		| ((data: { actionIndex: number; verdict: 'approved' | 'rejected'; reason?: string }) => void)
		| undefined = undefined;

	// Deduplicate sources by (source_type, title)
	$: uniqueSources = (() => {
		const seen = new Set<string>();
		return sources.filter(s => {
			const key = `${s.source_type}:${s.title}`;
			if (seen.has(key)) return false;
			seen.add(key);
			return true;
		});
	})();

	function getSourceBadgeColor(type: string): string {
		switch (type) {
			case 'model': return 'bg-info/10 text-info border-info/25';
			case 'prompt': return 'bg-signal/10 text-signal border-signal/25';
			case 'preset': return 'bg-warning/10 text-warning border-warning/25';
			case 'segment': return 'bg-success/10 text-success border-success/25';
			case 'phrasebook': return 'bg-info/10 text-info border-info/25';
			case 'style': return 'bg-danger/10 text-danger border-danger/25';
			default: return 'bg-surface-2 text-fg-muted border-line';
		}
	}

	// Prompt feedback (thumbs up/down on proposed segment updates)
	let localPromptFeedback: Record<number, { verdict: 'approved' | 'rejected'; reason?: string }> = {};
	let reasonPanelFor: number | null = null;
	let reasonText = '';

	$: storedPromptFeedback = (metadata?.prompt_feedback || {}) as Record<
		string,
		{ verdict: 'approved' | 'rejected'; reason?: string }
	>;

	// Precomputed as a reactive statement (rather than a plain function called from
	// the `{#each}` below) so Svelte's compiler tracks `localPromptFeedback` and
	// `storedPromptFeedback` as dependencies. A function call from inside `{@const}`
	// gets compiled to an untracked read — reassigning `localPromptFeedback` on click
	// (or a fresh `metadata` prop after the API round-trip) would silently never
	// re-render the thumbs buttons.
	$: promptFeedbackByIndex = markdownResult.actions.map(
		(_, idx) => localPromptFeedback[idx] ?? storedPromptFeedback[String(idx)]
	);

	// Same reasoning as promptFeedbackByIndex above: a `$:`-derived array so
	// $appliedSegmentActions reassignments (a new object each `set`/`clear`)
	// are tracked, instead of an untracked function call from inside `{@const}`.
	$: appliedByIndex = markdownResult.actions.map((_, idx) =>
		isAppliedSegmentAction($appliedSegmentActions, messageId, idx)
	);

	function submitPromptFeedback(actionIndex: number, verdict: 'approved' | 'rejected', reason?: string) {
		localPromptFeedback = { ...localPromptFeedback, [actionIndex]: { verdict, reason } };
		reasonPanelFor = null;
		reasonText = '';
		onPromptFeedback?.({ actionIndex, verdict, reason });
	}

	function toggleReasonPanel(actionIndex: number) {
		if (reasonPanelFor === actionIndex) {
			reasonPanelFor = null;
			reasonText = '';
		} else {
			reasonPanelFor = actionIndex;
			reasonText = '';
		}
	}

	function formatTime(ts: number): string {
		const date = new Date(ts);
		return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
	}

	// Split proposed segment content into plain text and #phrasebook marker tokens
	// (same pattern as chipParser.ts) so markers render as chips in the preview
	function splitMarkerTokens(text: string): Array<{ text: string; isMarker: boolean }> {
		const tokens: Array<{ text: string; isMarker: boolean }> = [];
		const markerRegex = /#\[[^\]]+\]|#[\w][\w.]*/g;
		let lastIndex = 0;
		let match: RegExpExecArray | null;
		while ((match = markerRegex.exec(text)) !== null) {
			if (match.index > lastIndex) {
				tokens.push({ text: text.slice(lastIndex, match.index), isMarker: false });
			}
			tokens.push({ text: match[0], isMarker: true });
			lastIndex = match.index + match[0].length;
		}
		if (lastIndex < text.length) {
			tokens.push({ text: text.slice(lastIndex), isMarker: false });
		}
		return tokens;
	}

	$: variableChips = buildVariableChipTooltips(variables, variableRolls);

	// Mid-stream, `content` is the raw accumulated token text and can still
	// carry a `## improved` / `## questions` marker the backend hasn't
	// cleaned yet (cleanup only happens on `done`) — cut the displayed prose
	// there so the marker never flashes before the settled copy replaces it.
	$: displayContent =
		role === 'assistant' && isStreaming ? truncateAtReplyContractMarker(content) : content;

	$: markdownResult =
		role === 'assistant'
			? processMarkdownWithActions(displayContent, { variableChips })
			: { html: '', actions: [], toolCalls: [] };

	$: replyContract = parsedContent?.reply_contract;

	// Leaked `<tool_call>` tags (old transcripts, older backends, a mid-stream
	// partial write) render as a quiet chip instead of raw tag + JSON — see
	// injectToolCallChips. isStreaming decides the unclosed-span state.
	$: renderedHtml = injectToolCallChips(markdownResult.html, markdownResult.toolCalls, isStreaming);

	// @resource chips in user messages: label comes from the resolved snapshot
	// the backend stored in message metadata; falls back to the raw uri.
	$: messageResources = (metadata?.resources || []) as ResourceRef[];

	function resourceLabel(uri: string): string {
		const match = messageResources.find((r) => r.uri === uri);
		return match?.title || match?.label || uri;
	}
</script>

<!-- User Message -->
{#if role === 'user'}
	<div class="border-l-2 border-line-strong/60 pl-3">
		<div class="{compact ? 'mb-1' : 'mb-1.5'}">
			<span class="font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-subtle">YOU</span>
			{#if timestamp}
				<span class="font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-disabled"> · {formatTime(timestamp)}</span>
			{/if}
		</div>
		<div class="{compact ? 'text-xs' : 'text-sm'} text-fg-muted whitespace-pre-wrap">
			{#each splitResourceTokens(content) as part}{#if part.type === 'resource'}<span class="text-signal bg-signal/10 border border-signal/25 rounded px-1" title={part.value}>@{resourceLabel(part.value)}</span>{:else}{part.value}{/if}{/each}
			{#if imageUrl}
				<div class="mt-2">
					<div class="flex items-center gap-1.5 mb-1.5">
						<svg class="w-3 h-3 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
						</svg>
						<span class="text-[10px] text-fg-subtle font-medium">Attached image</span>
					</div>
					<img
						src={imageUrl}
						alt="Attached"
						class="max-w-[200px] max-h-[200px] rounded-lg border border-line object-cover"
						loading="lazy"
					/>
				</div>
			{/if}
		</div>
	</div>

<!-- Assistant Message -->
{:else if role === 'assistant'}
	<div>
		<div class="{compact ? 'mb-1' : 'mb-1.5'}">
			<span class="font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-subtle">ASSISTANT</span>
			{#if timestamp}
				<span class="font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-disabled"> · {formatTime(timestamp)}</span>
			{/if}
		</div>

		<!-- Card container for AI response -->
		<div class="rounded-lg bg-surface-1 border border-line overflow-hidden">
				<!-- Plain assistant message -->
				<div class="{compact ? 'px-3 py-2 text-xs' : 'px-4 py-3 text-sm'} text-fg-muted leading-normal max-w-none">
					{@html renderedHtml}{#if isStreaming}<span class="inline-block w-2 h-4 ml-0.5 bg-signal animate-pulse rounded-sm"></span>{/if}
				</div>
				{#if replyContract?.improved?.length}
					<div class="border-t border-line {compact ? 'px-3 py-2' : 'px-4 py-3'}">
						<div class="font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-subtle mb-1.5">IMPROVED</div>
						<ul class="space-y-1">
							{#each replyContract.improved as line}
								<li class="flex items-start gap-1.5 text-sm text-fg-muted">
									<span class="mt-1.5 w-1 h-1 rounded-full bg-fg-subtle flex-shrink-0" aria-hidden="true"></span>
									<span>{line}</span>
								</li>
							{/each}
						</ul>
					</div>
				{/if}
				{#if markdownResult.actions.length > 0}
					<div class="border-t border-line">
						{#each markdownResult.actions as action, i}
							{@const feedback = promptFeedbackByIndex[i]}
							{@const applied = appliedByIndex[i]}
							<div
								class="{compact ? 'px-3 py-2' : 'px-4 py-3'} {i > 0 ? 'border-t border-line' : ''} {applied
									? 'ring-1 ring-inset ring-signal/40 bg-signal/5'
									: ''}"
							>
								<div class="flex items-center justify-between mb-2">
									<span class="text-xs font-semibold text-signal uppercase tracking-wider flex items-center gap-1.5">
										<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
											<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
										</svg>
										{action.type === 'update_director_segment' ? 'Director Segment' : 'Update Segment'} #{action.segmentIndex + 1}
									</span>
									<div class="flex items-center gap-2">
										{#if onPromptFeedback && sessionId && messageId}
											<div class="flex items-center gap-1">
												<button
													class="p-1 rounded border transition-colors {feedback?.verdict === 'approved'
														? 'bg-signal/10 border-signal/40 text-signal'
														: 'border-transparent text-fg-subtle hover:text-signal hover:bg-signal/10'}"
													disabled={!!feedback}
													title="Good prompt"
													aria-pressed={feedback?.verdict === 'approved'}
													on:click={() => submitPromptFeedback(i, 'approved')}
												>
													<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
														<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 10h4.764a2 2 0 011.789 2.894l-3.5 7A2 2 0 0115.263 21h-4.017c-.163 0-.326-.02-.485-.06L7 20m7-10V5a2 2 0 00-2-2h-.095c-.5 0-.905.405-.905.905 0 .714-.211 1.412-.608 2.006L7 11v9m7-10h-7m0 10H5a2 2 0 01-2-2v-6a2 2 0 012-2h2" />
													</svg>
												</button>
												<button
													class="p-1 rounded border transition-colors {feedback?.verdict === 'rejected'
														? 'bg-signal/10 border-signal/40 text-signal'
														: 'border-transparent text-fg-subtle hover:text-signal hover:bg-signal/10'}"
													disabled={!!feedback}
													title="Bad prompt"
													aria-pressed={feedback?.verdict === 'rejected'}
													on:click={() => toggleReasonPanel(i)}
												>
													<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
														<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14H5.236a2 2 0 01-1.789-2.894l3.5-7A2 2 0 018.737 3h4.017c.163 0 .326.02.485.06L17 4m-7 10v5a2 2 0 002 2h.095c.5 0 .905-.405.905-.905 0-.714.211-1.412.608-2.006L17 13V4m-7 10h7m0-10h2a2 2 0 012 2v6a2 2 0 01-2 2h-2" />
													</svg>
												</button>
											</div>
										{/if}
										{#if onApplyAction}
											<button
												class="px-3 py-1.5 text-xs font-medium rounded transition-colors flex items-center gap-1.5 {applied
													? 'text-signal bg-signal/10 border border-signal/40 hover:bg-signal/15'
													: 'text-accent-contrast bg-accent hover:bg-accent-hover'}"
												title={applied ? 'Re-apply this variant' : applyActionHint}
												on:click={() => onApplyAction?.(action, i)}
											>
												{#if applied}
													<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
														<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
													</svg>
												{/if}
												{applied ? 'Applied' : 'Apply'}
											</button>
										{/if}
									</div>
								</div>
								{#if reasonPanelFor === i && !feedback}
									<div class="flex items-center gap-1.5 mb-2" transition:slide={{ duration: 150 }}>
										<input
											type="text"
											bind:value={reasonText}
											placeholder="Optional reason (why is this prompt bad?)"
											class="flex-1 text-xs bg-canvas border border-line rounded px-2 py-1 text-fg-muted placeholder-fg-subtle focus:outline-none focus:border-line-strong"
											on:keydown={(e) => e.key === 'Enter' && submitPromptFeedback(i, 'rejected', reasonText.trim() || undefined)}
										/>
										<button
											class="px-2 py-1 text-xs font-medium text-white bg-danger-solid rounded hover:bg-danger-solid/90 transition-colors"
											on:click={() => submitPromptFeedback(i, 'rejected', reasonText.trim() || undefined)}
										>
											Confirm
										</button>
										<button
											class="px-2 py-1 text-xs font-medium text-fg-muted hover:text-fg-muted transition-colors"
											on:click={() => toggleReasonPanel(i)}
										>
											Cancel
										</button>
									</div>
								{/if}
								<div class="text-xs text-fg-muted bg-canvas rounded-lg p-2.5 font-mono whitespace-pre-wrap border border-line">
									{#each splitMarkerTokens(action.content) as token}{#if token.isMarker}<span class="text-signal bg-signal/15 rounded px-1">{token.text}</span>{:else}{token.text}{/if}{/each}
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</div>
			{#if uniqueSources.length > 0}
				<div class="mt-2.5 flex gap-2 overflow-x-auto pb-1 scrollbar-thin scrollbar-thumb-[rgb(var(--line-strong))] scrollbar-track-transparent">
					{#each uniqueSources as source}
						<div class="flex-shrink-0 w-48 rounded-lg bg-surface-1 border border-line p-2.5 hover:border-line-strong transition-colors">
							<div class="flex items-center gap-1.5 mb-1">
								<span class="px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase border {getSourceBadgeColor(source.source_type)}">
									{source.source_type}
								</span>
							</div>
							<div class="text-[11px] font-medium text-fg-muted truncate" title={source.title}>
								{source.title}
							</div>
							{#if source.subtitle}
								<div class="text-[10px] text-fg-subtle truncate mt-0.5">
									{source.subtitle}
								</div>
							{/if}
							{#if source.description}
								<div class="text-[10px] text-fg-subtle mt-1 line-clamp-2 leading-relaxed">
									{source.description}
								</div>
							{/if}
							{#if source.url}
								<a
									href={source.url}
									target="_blank"
									rel="noopener noreferrer"
									class="inline-flex items-center gap-1 text-[10px] text-signal hover:text-signal mt-1.5 transition-colors"
								>
									<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
									</svg>
									Source
								</a>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
			<!-- Behavior trace: tool status chips + context steps, expandable details (display-only) -->
			<ChatBehaviorTrace executions={toolExecutions} {traceSteps} {metadata} {isStreaming} />
	</div>

<!-- System Message -->
{:else if role === 'system'}
	<div>
		<div class="{compact ? 'mb-1' : 'mb-1.5'}">
			<span class="font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-subtle">SYSTEM</span>
			{#if timestamp}
				<span class="font-mono text-2xs font-medium uppercase tracking-[0.07em] text-fg-disabled"> · {formatTime(timestamp)}</span>
			{/if}
		</div>
		<div class="{compact ? 'text-xs' : 'text-sm'} text-warning bg-warning/10 rounded-lg {compact ? 'px-3 py-2' : 'px-4 py-3'} border border-line leading-normal max-w-none">
			{@html processMarkdown(content, { variableChips })}
		</div>
	</div>
{/if}
