<script lang="ts">
	/**
	 * The behavior trace under an assistant message: tool calls compress into a
	 * single collapsible record (ChatToolRun); context steps (resources read,
	 * memory recalled, pre-chat actions, thinking, answering) stay in a
	 * separate, collapsible timeline below it. Tool APPROVAL is a separate
	 * surface (ApprovalDock, docked above the composer) — a pending execution
	 * here is display-only (ChatToolRun excludes it entirely). No
	 * arguments/results render for a tool beyond a failed row's one-line
	 * error: showing more in the user-facing chat can leak internal data, so
	 * full detail lives only in Admin -> LLM / Sessions.
	 */
	import { slide } from 'svelte/transition';
	import type { ToolExecution, TraceStep, TraceStepName } from '$lib/types/chat';
	import { hydrateTraceSteps, formatContextLedgerSummary, sumMemoryDropped } from '$lib/utils/chatStream';
	import ChatToolRun from './ChatToolRun.svelte';
	import Icon from '$lib/components/Icon.svelte';

	export let executions: ToolExecution[] = [];
	export let traceSteps: TraceStep[] = [];
	export let metadata: Record<string, any> | undefined = undefined;
	export let isStreaming: boolean = false;

	// Live steps take priority; once a message finishes without ever streaming
	// them (e.g. page reload), reconstruct from the persisted manifest.
	$: manifest = metadata?.behavior_trace;
	let effectiveSteps: TraceStep[] = [];
	$: effectiveSteps = traceSteps.length > 0 ? traceSteps : hydrateTraceSteps(manifest, executions.length);

	const STEP_ICON: Record<TraceStepName, string> = {
		resolving_resources: 'book-open',
		tools: 'warning',
		loading_memory: 'brain',
		running_pre_chat: 'zap',
		thinking: 'lightbulb',
		answering: 'pencil'
	};

	// Copy for a withheld tool's reason — mirrors
	// context_builder.py's _TOOL_UNAVAILABLE_LABELS.
	const TOOL_WITHHELD_LABEL: Record<string, string> = {
		off_by_toggle: 'Tools off',
		disabled_in_mode: 'off for this mode',
		disabled_by_admin: 'disabled by admin',
		opted_out: 'off in your preferences',
		unavailable: 'not available now'
	};
	// Fallback for step names outside the current TraceStepName union — e.g. a
	// persisted manifest from before a step was retired (older behavior traces
	// may still carry it).
	const FALLBACK_STEP_ICON = 'information-circle';

	// "Currently active" flag per context step, index-aligned with `effectiveSteps`.
	// `isStreaming` flips false when a turn finishes, and `effectiveSteps` grows as
	// new steps append; this is a `$:` statement (not a plain function called from
	// `{@const}`) so Svelte's dependency scan sees both directly — a function call
	// hides those reads and an already-rendered row's pulsing "active" indicator
	// would stay stuck on even after streaming ends or a later row becomes the new
	// last item.
	$: activeByIndex = effectiveSteps.map((step, idx) => {
		if (!isStreaming || idx !== effectiveSteps.length - 1) return false;
		return step.state === 'started';
	});

	function stepLabel(step: TraceStep): string {
		const detail = step.detail || {};
		switch (step.step) {
			case 'resolving_resources':
				return step.state === 'completed'
					? `Reading attached resources (${detail.count ?? 0})`
					: 'Reading attached resources…';
			case 'loading_memory':
				return step.state === 'completed'
					? `Recalling memory (${detail.note_count ?? 0} notes)`
					: 'Recalling memory…';
			case 'running_pre_chat':
				return step.state === 'completed'
					? `Preparing (${(detail.actions || []).join(', ')})`
					: 'Preparing…';
			case 'tools': {
				const withheldCount = Object.keys(detail.withheld || {}).length;
				return (detail.offered || []).length === 0 ? 'Tools off' : `Tools: ${withheldCount} unavailable`;
			}
			case 'thinking':
				return 'Thinking…';
			case 'answering':
				return 'Writing answer';
			default:
				return step.step;
		}
	}

	function stepSummary(step: TraceStep): string {
		if (step.step === 'resolving_resources' && step.detail?.uris?.length) {
			return step.detail.uris.join(', ');
		}
		if (step.step === 'running_pre_chat' && step.detail?.actions?.length) {
			return step.detail.actions.join(', ');
		}
		if (step.step === 'tools' && step.detail?.withheld) {
			return Object.keys(step.detail.withheld).join(', ');
		}
		return '';
	}

	let expandedSteps: Record<number, boolean> = {};
	function toggleStep(seq: number) {
		expandedSteps = { ...expandedSteps, [seq]: !expandedSteps[seq] };
	}

	// Collapsed by default once a message is finished; expanded while streaming.
	let userToggled: boolean | null = null;
	$: showList = userToggled === null ? isStreaming : userToggled;

	$: tokenCounts = manifest?.token_counts;
	// Some providers/streams never report usage, so prompt/completion can each
	// legitimately be null even when token_counts itself is present — only
	// render the summary once both numbers are actually known.
	$: hasTokenCounts = typeof tokenCounts?.prompt === 'number' && typeof tokenCounts?.completion === 'number';
	$: modeLabel = manifest?.mode;

	// Absent on manifests persisted before the context ledger existed — the
	// footer guards on `contextLedger` so an older trace renders nothing here,
	// never a NaN breakdown.
	$: contextLedger = manifest?.context_ledger;
	$: memoryDroppedCount = sumMemoryDropped(manifest?.memory?.by_scope_dropped);
	$: toolFailureEntries = Object.entries(manifest?.tool_failures ?? {});
</script>

<ChatToolRun {executions} />

{#if effectiveSteps.length > 0}
	<div class="behavior-trace">
		<button
			type="button"
			class="behavior-trace-toggle"
			class:open={showList}
			on:click={() => (userToggled = !showList)}
		>
			<svg class="icon"><use href="#i-chevron" /></svg>
			<span>{effectiveSteps.length} steps</span>
			{#if !showList}
				<span class="truncate max-w-[420px]">· {effectiveSteps.map(stepLabel).join(', ')}</span>
			{/if}
		</button>

		{#if showList}
			<div class="behavior-trace-list" transition:slide={{ duration: 150 }}>
				{#each effectiveSteps as step, idx (step.seq ?? idx)}
					{@const active = activeByIndex[idx] ?? false}
					{@const seq = step.seq ?? idx}
					{@const summary = stepSummary(step)}
					<div class="min-w-0">
						<button type="button" class="behavior-trace-step" on:click={() => toggleStep(seq)}>
							{#if active}
								<span class="tool-execution-spinner" role="status" aria-label="Active"></span>
							{:else}
								<span class="behavior-trace-dot" title="Completed"></span>
							{/if}
							<Icon name={STEP_ICON[step.step] ?? FALLBACK_STEP_ICON} className="w-3 h-3 flex-shrink-0" />
							<span class="flex-shrink-0">{stepLabel(step)}</span>
							<span class="flex-1 truncate text-fg-subtle">{summary}</span>
							{#if step.duration_ms != null}
								<span class="font-mono tabular-nums text-2xs text-fg-disabled flex-shrink-0"
									>{step.duration_ms}ms</span
								>
							{/if}
						</button>
						{#if expandedSteps[seq] && (summary || step.step === 'loading_memory')}
							<div class="behavior-trace-detail" transition:slide={{ duration: 150 }}>
								{#if step.step === 'resolving_resources' && step.detail?.uris}
									{#each step.detail.uris as uri}
										<div class="truncate">{uri}</div>
									{/each}
								{:else if step.step === 'loading_memory' && step.detail?.by_scope}
									<div>
										global {step.detail.by_scope.global ?? 0} · mode {step.detail.by_scope.mode ?? 0} · preset {step
											.detail.by_scope.preset ?? 0} · model {step.detail.by_scope.model ?? 0}
									</div>
								{:else if step.step === 'running_pre_chat' && step.detail?.actions}
									{#each step.detail.actions as action}
										<div>{action}</div>
									{/each}
								{:else if step.step === 'tools' && step.detail?.withheld}
									{#each Object.entries(step.detail.withheld as Record<string, string>) as [name, reason]}
										<div>{name} — {TOOL_WITHHELD_LABEL[reason] ?? reason}</div>
									{/each}
								{/if}
							</div>
						{/if}
					</div>
				{/each}
			</div>
			{#if modeLabel || hasTokenCounts}
				<div class="behavior-trace-footer">
					{#if modeLabel}{modeLabel}{/if}{#if modeLabel && hasTokenCounts} · {/if}{#if hasTokenCounts}{tokenCounts.prompt.toLocaleString()}
						prompt / {tokenCounts.completion.toLocaleString()} completion tokens{/if}
				</div>
			{/if}
			{#if contextLedger}
				<div class="behavior-trace-footer">{formatContextLedgerSummary(contextLedger)}</div>
			{/if}
			{#if memoryDroppedCount > 0}
				<div class="behavior-trace-footer">
					{memoryDroppedCount} note{memoryDroppedCount === 1 ? '' : 's'} over cap, not injected
				</div>
			{/if}
			{#if toolFailureEntries.length > 0}
				<div class="behavior-trace-footer" style="color: rgb(var(--danger));">
					{toolFailureEntries.map(([name, count]) => `${name} failed ×${count}`).join(', ')}
				</div>
			{/if}
		{/if}
	</div>
{/if}
