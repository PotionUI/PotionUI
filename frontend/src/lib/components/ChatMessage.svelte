<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import delegatedTooltip from '$lib/actions/delegatedTooltip';
	import { slide } from 'svelte/transition';
	import {
		processMarkdown,
		processMarkdownWithActions,
		injectToolCallChips,
		truncateAtReplyContractMarker,
		isPromptListAction,
		type ToolAction,
		type ToolCallSpan
	} from '$lib/utils/markdown';
	import { promptActionHeading } from '$lib/chat/promptActions';
	import { promptActionUndos } from '$lib/stores/promptActionUndos';
	import type { ToolExecution, ResourceRef, TraceStep, ReplyContract, ReasoningBlock, ChatNotice } from '$lib/types/chat';
	import { splitResourceTokens } from '$lib/utils/resourceTokens';
	import { splitMarkerTokens } from '$lib/chat/previewTokens';
	import type { PromptResourceSpec } from '$lib/utils/promptResources';
	import { resourceTokenWarnings, splitResourceTokenPreview } from '$lib/utils/promptResourceTokens';
	import { buildVariableChipTooltips } from '$lib/utils/variableSnapshot';
	import type { VariablesMap, VariableRoll } from '$lib/utils/variableDefs';
	import ChatBehaviorTrace from '$lib/components/chat/ChatBehaviorTrace.svelte';
	import ChatReasoningBlock from '$lib/components/chat/ChatReasoningBlock.svelte';
	import ChatNotices from '$lib/components/chat/ChatNotices.svelte';
	import ChatActionCard from '$lib/components/chat/ChatActionCard.svelte';
	import ConditionChip from '$lib/components/ConditionChip.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { readable } from 'svelte/store';
	import { conditionTagProblems, splitConditionText } from '$lib/chat/conditionText';
	import { actionExecutions, type ActionDecision, type ActionResolveOutcome } from '$lib/chat/actionCard';
	import Logo from '$lib/components/brand/Logo.svelte';
	import { copyText } from '$lib/utils/clipboard';
	import { appliedSegmentActions, isAppliedSegmentAction } from '$lib/stores/appliedSegmentActions';
	import { removedSegmentActions, removedActionKey } from '$lib/stores/removedSegmentActions';
	import Icon from '$lib/components/Icon.svelte';

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
	export let reasoning: ReasoningBlock[] = [];
	export let notices: ChatNotice[] = [];
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
	export let promptResources: PromptResourceSpec[] = [];
	export let resourceFormValues: Record<string, unknown> = {};
	export let resourceFieldLabels: Record<string, string> = {};
	export let onApplyAction:
		| ((
				action: { type: string; segmentIndex: number; segmentId: string; content: string },
				actionIndex: number
		  ) => void)
		| undefined = undefined;
	// Optional tooltip clarifying where "Apply" writes to when that isn't the
	// obvious flat segment list — e.g. Video Director's persistent Direction prompt.
	export let applyActionHint: string | undefined = undefined;
	export let onUndoAction: ((actionIndex: number) => void) | undefined = undefined;
	export let listActionsDisabled: boolean = false;
	export let batchPromptCount: number = 0;
	export let segmentTextFor: ((action: ToolAction) => string | null) | undefined = undefined;
	export let onPromptFeedback:
		| ((data: { actionIndex: number; verdict: 'approved' | 'rejected'; reason?: string }) => void)
		| undefined = undefined;
	export let actionsLocked: boolean = false;
	export let actionErrors: Record<string, string> = {};
	export let onResolveAction:
		| ((index: number, decision: ActionDecision) => Promise<ActionResolveOutcome>)
		| undefined = undefined;
	export let onAskRedo: ((index: number) => void) | undefined = undefined;

	$: actionItems = actionExecutions(toolExecutions);
	$: conditionContext = readable({ variables: variables ?? {}, rolls: variableRolls ?? {} });

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

	$: conditionProblemsByIndex = markdownResult.actions.map((action) =>
		action.type === 'delete_segment' ? [] : conditionTagProblems(action.content)
	);

	$: removalByIndex = markdownResult.actions.map(
		(_, idx) => $removedSegmentActions[removedActionKey(messageId, idx)]
	);

	$: promptUndoByIndex = markdownResult.actions.map((_, idx) => $promptActionUndos[removedActionKey(messageId, idx)]);

	function promptActionNumber(index: number): number {
		const undo = promptUndoByIndex[index];
		return undo?.kind === 'add' ? undo.index + 1 : batchPromptCount + 1;
	}

	function promptActionBody(action: ToolAction): string {
		const number = (action.promptIndex ?? 0) + 1;
		if (action.type === 'select_prompt') return `Makes prompt ${number} the active prompt.`;
		if (action.type === 'remove_prompt') return `Removes prompt ${number} and its segments.`;
		if (action.type === 'rename_prompt') return `Renames prompt ${number} to "${action.name}".`;
		return 'Adds an empty prompt.';
	}

	function actionHeading(action: ToolAction, index = 0): string {
		if (isPromptListAction(action.type)) return promptActionHeading(action, promptActionNumber(index));
		if (action.type === 'update_director_segment') return 'Director Segment';
		if (action.type === 'add_segment') return action.list === 'negative' ? 'Add Negative Segment' : 'Add Segment';
		if (action.type === 'delete_segment') return 'Delete Segment';
		return 'Update Segment';
	}

	function actionPosition(action: ToolAction): string {
		if (isPromptListAction(action.type)) return '';
		const position = action.type === 'add_segment' || action.segmentIndex < 0 ? '' : ` #${action.segmentIndex + 1}`;
		return action.promptIndex !== undefined ? `${position} · Prompt ${action.promptIndex + 1}` : position;
	}

	function actionTag(action: ToolAction): string {
		if (action.type === 'add_segment' || action.type === 'add_prompt') return 'Suggested addition';
		if (action.type === 'delete_segment' || action.type === 'remove_prompt') return 'Suggested removal';
		if (action.type === 'select_prompt') return 'Suggested switch';
		if (action.type === 'rename_prompt') return 'Suggested rename';
		return 'Suggested change';
	}

	function isReapplicable(action: ToolAction): boolean {
		return action.type !== 'add_segment' && action.type !== 'delete_segment' && !isPromptListAction(action.type);
	}

	function carriesPromptText(action: ToolAction): boolean {
		return action.type !== 'delete_segment' && (!isPromptListAction(action.type) || action.content.trim().length > 0);
	}

	function applyLabel(action: ToolAction, applied: boolean): string {
		if (action.type === 'add_segment' || action.type === 'add_prompt') return applied ? '✓ Added' : 'Add';
		if (action.type === 'delete_segment') return applied ? '✓ Deleted' : 'Delete';
		if (action.type === 'remove_prompt') return applied ? '✓ Removed' : 'Remove';
		if (action.type === 'select_prompt') return applied ? '✓ Switched' : 'Switch';
		if (action.type === 'rename_prompt') return applied ? '✓ Renamed' : 'Rename';
		return applied ? '✓ Applied' : 'Apply';
	}

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

	// Memoized on (displayContent, variableChips) identity rather than left as
	// a plain `$:` dependency, as a cheap, explicit guarantee that this
	// component never re-runs `processMarkdownWithActions` (two regex passes
	// plus a full markdown render) for a value it already parsed.
	let markdownResult: { html: string; actions: ToolAction[]; toolCalls: ToolCallSpan[] } = {
		html: '',
		actions: [],
		toolCalls: []
	};
	let memoizedContent: string | undefined;
	let memoizedVariableChips: Record<string, string> | undefined;
	let memoizedStreaming: boolean | undefined;
	$: {
		if (role === 'assistant') {
			if (displayContent !== memoizedContent || variableChips !== memoizedVariableChips || isStreaming !== memoizedStreaming) {
				memoizedContent = displayContent;
				memoizedVariableChips = variableChips;
				memoizedStreaming = isStreaming;
				markdownResult = processMarkdownWithActions(displayContent, { variableChips }, isStreaming);
			}
		} else if (memoizedContent !== undefined) {
			memoizedContent = undefined;
			memoizedVariableChips = undefined;
			markdownResult = { html: '', actions: [], toolCalls: [] };
		}
	}

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
			{#each splitResourceTokens(content) as part}{#if part.type === 'resource'}<span class="prompt-token" use:tooltip={part.value}>@{resourceLabel(part.value)}</span>{:else}{part.value}{/if}{/each}
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
			<ChatNotices {notices} />
			<ChatReasoningBlock blocks={reasoning} {isStreaming} />
			<div class="assistant-copy" use:delegatedTooltip>
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

				{#each actionItems as item (item.index)}
					<ChatActionCard
						execution={item.execution}
						actionKey="{messageId || 'local'}:{item.index}"
						variables={variables}
						rolls={variableRolls}
						locked={actionsLocked || !messageId}
						externalError={actionErrors[`${messageId}:${item.index}`] ?? null}
						onResolve={onResolveAction ? (decision) => onResolveAction!(item.index, decision) : undefined}
						onAskRedo={onAskRedo ? () => onAskRedo!(item.index) : undefined}
					/>
				{/each}

				{#each markdownResult.actions as action, i}
					{@const feedback = promptFeedbackByIndex[i]}
					{@const applied = appliedByIndex[i]}
					<div class="prompt-card">
						<div class="prompt-card-head">
							<div class="prompt-card-title">
								<Icon name="chat-edit" className="icon" strokeWidth={1.8} />
								<strong>{actionHeading(action, i)}{actionPosition(action)}</strong>
								<span class="prompt-label">{actionTag(action)}</span>
							</div>
							<div class="flex flex-shrink-0 items-center gap-2">
								{#if onPromptFeedback && sessionId && messageId && carriesPromptText(action)}
									<button
										type="button"
										class="message-action"
										class:active={feedback?.verdict === 'approved'}
										disabled={!!feedback}
										use:tooltip={'Good prompt'}
										aria-pressed={feedback?.verdict === 'approved'}
										on:click={() => submitPromptFeedback(i, 'approved')}
										aria-label="Good prompt"
									>
										<Icon name="chat-good" className="icon" strokeWidth={1.8} />
									</button>
									<button
										type="button"
										class="message-action"
										class:active={feedback?.verdict === 'rejected'}
										disabled={!!feedback}
										use:tooltip={'Bad prompt'}
										aria-pressed={feedback?.verdict === 'rejected'}
										on:click={() => toggleReasonPanel(i)}
										aria-label="Bad prompt"
									>
										<Icon name="chat-bad" className="icon" strokeWidth={1.8} />
									</button>
								{/if}
								{#if onApplyAction}
									{#if applied && onUndoAction && ((action.type === 'delete_segment' && removalByIndex[i]) || promptUndoByIndex[i])}
										<button
											type="button"
											class="undo-button"
											data-testid="segment-action-undo"
											on:click={() => onUndoAction?.(i)}
										>
											Undo
										</button>
									{/if}
									<button
										type="button"
										class="apply-button {applied ? '' : 'disabled:opacity-50 disabled:cursor-not-allowed'}"
										class:applied
										class:destructive={(action.type === 'delete_segment' || action.type === 'remove_prompt') && !applied}
										data-testid="segment-action-apply"
										disabled={conditionProblemsByIndex[i].length > 0 || (!isReapplicable(action) && (applied || listActionsDisabled))}
										use:tooltip={applied ? (isReapplicable(action) ? 'Re-apply this variant' : undefined) : applyActionHint}
										on:click={() => conditionProblemsByIndex[i].length === 0 && onApplyAction?.(action, i)}
									>
										{applyLabel(action, applied)}
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
						{#if action.type === 'delete_segment'}
							<div class="prompt-copy prompt-copy-removed" data-testid="segment-removed-text">
								{removalByIndex[i]?.segment.content ?? segmentTextFor?.(action) ?? ''}
							</div>
						{:else if isPromptListAction(action.type) && !action.content.trim()}
							<div class="prompt-copy prompt-copy-note" data-testid="prompt-action-note">{promptActionBody(action)}</div>
						{:else}
						<div class="prompt-copy">
							{#each splitConditionText(action.content) as part, partIndex (partIndex)}{#if part.kind === 'condition'}{#key part.raw}<ConditionChip raw={part.raw} context={conditionContext} readonly checkVariables={variables !== undefined} />{/key}{:else}{#each splitMarkerTokens(part.text) as token}{#if token.kind === 'phrasebook'}<Tooltip text="Phrasebook: {token.label}" wrapperClass="inline-flex"><span class="prompt-token"><span class="prompt-token-mark">#</span>{token.label}</span></Tooltip>{:else if token.kind === 'variable'}<Tooltip text={variableChips[token.label] ?? `Variable: ${token.label}`} wrapperClass="inline-flex"><span class="prompt-token variable"><span class="prompt-token-mark">$</span>{token.label}</span></Tooltip>{:else}{#each splitResourceTokenPreview(token.text, promptResources, resourceFormValues) as part}{#if part.kind === 'linked'}<span class="prompt-token resource" data-resource-token="linked"><span class="prompt-token-mark">@</span>{part.label}</span>{:else if part.kind === 'unresolved'}<span class="prompt-token-unresolved" data-resource-token="unresolved">{part.text}</span>{:else}{part.text}{/if}{/each}{/if}{/each}{/if}{/each}
						</div>
						{/if}
						{#if conditionProblemsByIndex[i].length}
							<div class="prompt-resource-warning" role="alert" data-testid="condition-tag-problems">
								<strong>Apply is off until the condition tags are fixed.</strong>
								{#each conditionProblemsByIndex[i] as problem (problem.kind + problem.offset)}
									<div>{problem.message}</div>
								{/each}
							</div>
						{/if}
						{#if promptResources.length}
							{#each resourceTokenWarnings(action.content, { specs: promptResources, formValues: resourceFormValues, fieldLabels: resourceFieldLabels }) as warning (warning)}
								<p class="prompt-resource-warning" data-resource-warning>{warning}</p>
							{/each}
						{/if}
					</div>
				{/each}

				{#if uniqueSources.length > 0}
					<div class="message-sources">
						{#each uniqueSources as source}
							<div class="message-source-card">
								<span class="message-source-badge">{source.source_type}</span>
								<div class="message-source-title" use:tooltip={source.title}>{source.title}</div>
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
				<button type="button" class="message-action" use:tooltip={'Copy'} aria-label="Copy response" on:click={copyResponse}>
					<Icon name={responseCopied ? 'chat-check' : 'chat-copy'} className="icon" strokeWidth={1.8} />
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
		<div class="{compact ? 'text-xs' : 'text-sm'} text-warning bg-warning/10 rounded-lg {compact ? 'px-3 py-2' : 'px-4 py-3'} border border-line leading-normal max-w-none" use:delegatedTooltip>
			{@html processMarkdown(content, { variableChips })}
		</div>
	</div>
{/if}
