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
	import { splitMarkerTokens } from '$lib/chat/previewTokens';
	import { buildVariableChipTooltips } from '$lib/utils/variableSnapshot';
	import type { VariablesMap, VariableRoll } from '$lib/utils/variableDefs';
	import ChatBehaviorTrace from '$lib/components/chat/ChatBehaviorTrace.svelte';
	import Logo from '$lib/components/brand/Logo.svelte';
	import { copyText } from '$lib/utils/clipboard';
	import { appliedSegmentActions, isAppliedSegmentAction } from '$lib/stores/appliedSegmentActions';

	export let role: 'user' | 'assistant' | 'system';
	export let content: string;
	export let timestamp: number | undefined = undefined;
	export let imageUrl: string | undefined = undefined;
	export let compact: boolean = false;
	export let isStreaming: boolean = false;
	// True while the displayed content is a bounded reconnect snapshot (the
	// backend compacted away the prefix this client expected), not the whole
	// reply — cleared once `done`/`error`/durable recovery settles the message.
	export let isPartial: boolean = false;
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

	// Prompt feedback (thumbs up/down on proposed segment updates)
	let localPromptFeedback: Record<number, { verdict: 'approved' | 'rejected'; reason?: string }> = {};
	let reasonPanelFor: number | null = null;
	let reasonText = '';

	// Response copy: check-swap for ~1.5s (see feedback_copy_feedback_standard).
	let responseCopied = false;
	let responseCopiedTimer: ReturnType<typeof setTimeout> | undefined;
	async function copyResponse() {
		const ok = await copyText(content);
		if (!ok) return;
		responseCopied = true;
		clearTimeout(responseCopiedTimer);
		responseCopiedTimer = setTimeout(() => (responseCopied = false), 1500);
	}

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

	// Best-effort file name for an attached image: the props carry only a URL
	// (no source/name metadata reaches this component), so the label line
	// never claims a source it can't verify — see attachmentSourceLabel.
	function attachmentName(url: string): string {
		try {
			const path = url.split(/[?#]/)[0];
			const base = decodeURIComponent(path.split('/').pop() || '');
			return base || 'image';
		} catch {
			return 'image';
		}
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

	// The final answer round hit its output limit (see clients/completion.py) —
	// distinct from `isPartial`, which is a transport/replay marker, not a
	// model outcome. Absent on messages persisted before this field existed,
	// and on any round whose visible content was a rescue's fallback message
	// rather than a genuine completion.
	$: hitOutputLimit = metadata?.behavior_trace?.completion?.reason === 'length';

	// Trailing "MODEL · N TOKENS" line: both come straight from the backend's
	// per-message metadata (conversation.py's assistant_metadata) — never
	// derived from the session's current model, which can differ from what
	// actually answered this turn.
	$: modelTokensLine = (() => {
		const model = typeof metadata?.model === 'string' ? metadata.model.toUpperCase() : '';
		const tokens = typeof metadata?.tokens_used === 'number' ? `${metadata.tokens_used.toLocaleString()} TOKENS` : '';
		return [model, tokens].filter(Boolean).join(' · ');
	})();
</script>

<!-- User Message -->
{#if role === 'user'}
	<article class="message user-message">
		<div class="user-bubble">
			{#each splitResourceTokens(content) as part}{#if part.type === 'resource'}<span class="prompt-token" title={part.value}>@{resourceLabel(part.value)}</span>{:else}{part.value}{/if}{/each}
		</div>
		{#if imageUrl}
			<div class="user-attachment">
				<img src={imageUrl} alt="Attached" loading="lazy" />
				<div class="user-attachment-copy">
					<strong>{attachmentName(imageUrl)}</strong>
					<span>Image attachment</span>
				</div>
			</div>
		{/if}
	</article>

<!-- Assistant Message -->
{:else if role === 'assistant'}
	<article class="message assistant-message">
		<div class="ai-avatar"><Logo size={18} /></div>
		<div class="assistant-body">
			<div class="assistant-meta">
				<strong>PotionAI</strong>
				{#if timestamp}<span>{formatTime(timestamp)}</span>{/if}
			</div>

			{#if isPartial}
				<div class="font-mono text-2xs text-fg-subtle" data-testid="partial-reply-note">
					Earlier part of this reply isn't shown — loading the full version&hellip;
				</div>
			{/if}
			{#if hitOutputLimit}
				<div class="font-mono text-2xs text-fg-subtle" data-testid="output-limit-note">
					Stopped at the output limit; the reply may be incomplete.
				</div>
			{/if}
			<div class="assistant-copy">
				{@html renderedHtml}{#if isStreaming}<span class="inline-block w-2 h-4 ml-0.5 bg-signal animate-pulse rounded-sm"></span>{/if}

				{#if replyContract?.improved?.length}
					<div class="reply-improved">
						<div class="reply-improved-label">Improved</div>
						<ul>
							{#each replyContract.improved as line}
								<li>{@html processMarkdown(line, { variableChips })}</li>
							{/each}
						</ul>
					</div>
				{/if}

				<ChatBehaviorTrace executions={toolExecutions} {traceSteps} {metadata} {isStreaming} />

				{#each markdownResult.actions as action, i}
					{@const feedback = promptFeedbackByIndex[i]}
					{@const applied = appliedByIndex[i]}
					<div class="prompt-card">
						<div class="prompt-card-head">
							<div class="prompt-card-title">
								<svg class="icon"><use href="#i-edit" /></svg>
								<strong
									>{action.type === 'update_director_segment' ? 'Director Segment' : 'Update Segment'} #{action.segmentIndex +
										1}</strong
								>
								<span class="prompt-label">Suggested change</span>
							</div>
							<div class="flex flex-shrink-0 items-center gap-2">
								{#if onPromptFeedback && sessionId && messageId}
									<button
										type="button"
										class="message-action"
										class:active={feedback?.verdict === 'approved'}
										disabled={!!feedback}
										title="Good prompt"
										aria-pressed={feedback?.verdict === 'approved'}
										on:click={() => submitPromptFeedback(i, 'approved')}
									>
										<svg class="icon"><use href="#i-good" /></svg>
									</button>
									<button
										type="button"
										class="message-action"
										class:active={feedback?.verdict === 'rejected'}
										disabled={!!feedback}
										title="Bad prompt"
										aria-pressed={feedback?.verdict === 'rejected'}
										on:click={() => toggleReasonPanel(i)}
									>
										<svg class="icon"><use href="#i-bad" /></svg>
									</button>
								{/if}
								{#if onApplyAction}
									<button
										type="button"
										class="apply-button"
										class:applied
										title={applied ? 'Re-apply this variant' : applyActionHint}
										on:click={() => onApplyAction?.(action, i)}
									>
										{applied ? '✓ Applied' : 'Apply'}
									</button>
								{/if}
							</div>
						</div>
						{#if reasonPanelFor === i && !feedback}
							<div class="prompt-feedback-reason" transition:slide={{ duration: 150 }}>
								<input
									type="text"
									bind:value={reasonText}
									placeholder="Optional reason (why is this prompt bad?)"
									on:keydown={(e) => e.key === 'Enter' && submitPromptFeedback(i, 'rejected', reasonText.trim() || undefined)}
								/>
								<button on:click={() => submitPromptFeedback(i, 'rejected', reasonText.trim() || undefined)}>Confirm</button>
								<button on:click={() => toggleReasonPanel(i)}>Cancel</button>
							</div>
						{/if}
						<div class="prompt-copy">
							{#each splitMarkerTokens(action.content) as token}{#if token.kind === 'phrasebook'}<span class="prompt-token" title="Phrasebook: {token.label}"><span class="prompt-token-mark">#</span>{token.label}</span>{:else if token.kind === 'variable'}<span class="prompt-token variable" title={variableChips[token.label] ?? `Variable: ${token.label}`}><span class="prompt-token-mark">$</span>{token.label}</span>{:else}{token.text}{/if}{/each}
						</div>
					</div>
				{/each}

				{#if uniqueSources.length > 0}
					<div class="message-sources">
						{#each uniqueSources as source}
							<div class="message-source-card">
								<span class="message-source-badge">{source.source_type}</span>
								<div class="message-source-title" title={source.title}>{source.title}</div>
								{#if source.subtitle}<div class="message-source-sub">{source.subtitle}</div>{/if}
								{#if source.url}
									<a href={source.url} target="_blank" rel="noopener noreferrer" class="message-source-sub"
										>Source ↗</a
									>
								{/if}
							</div>
						{/each}
					</div>
				{/if}
			</div>

			<div class="message-actions">
				<button type="button" class="message-action" title="Copy" aria-label="Copy response" on:click={copyResponse}>
					<svg class="icon"><use href={responseCopied ? '#i-check' : '#i-copy'} /></svg>
				</button>
				{#if modelTokensLine}
					<span class="message-model">{modelTokensLine}</span>
				{/if}
			</div>
		</div>
	</article>

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
