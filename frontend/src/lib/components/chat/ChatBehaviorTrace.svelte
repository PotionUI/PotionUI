<script lang="ts">
	/**
	 * The behavior trace under an assistant message: tool calls compress to an
	 * always-visible row of status chips (ChatToolChip); context steps
	 * (resources read, memory recalled, pre-chat actions, thinking, answering)
	 * stay in a separate, collapsible timeline below it. Tool APPROVAL is a
	 * separate surface (ApprovalDock, docked above the composer) — a pending
	 * chip here is display-only. No arguments/results render for a tool beyond
	 * a failed chip's one-line error: showing more in the user-facing chat can
	 * leak internal data, so full detail lives only in Admin -> LLM / Sessions.
	 */
	import { slide } from 'svelte/transition';
	import type { ToolExecution, ChatToolInfo, TraceStep, TraceStepName } from '$lib/types/chat';
	import { chatModes } from '$lib/stores/chatModes';
	import { hydrateTraceSteps, formatContextLedgerSummary, sumMemoryDropped } from '$lib/utils/chatStream';
	import ChatToolChip from './ChatToolChip.svelte';
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
		loading_memory: 'brain',
		running_pre_chat: 'zap',
		thinking: 'lightbulb',
		answering: 'pencil'
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
		return '';
	}

	function toolMetaFor(name: string, catalog: ChatToolInfo[]) {
		const info = catalog.find((t) => t.name === name);
		return info ? { icon: info.icon, label: info.label } : null;
	}

	let expandedSteps: Record<number, boolean> = {};
	function toggleStep(seq: number) {
		expandedSteps = { ...expandedSteps, [seq]: !expandedSteps[seq] };
	}

	// Which tool chip's error line (if any) is expanded. Cleared whenever the
	// execution list changes shape so a stale index can't point at the wrong tool.
	let expandedChipIndex: number | null = null;
	$: executions, (expandedChipIndex = null);
	$: expandedError =
		expandedChipIndex !== null ? executions[expandedChipIndex]?.result?.error : undefined;

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

{#if executions.length > 0}
	<div class="mt-2 flex flex-wrap items-center gap-1.5">
		{#each executions as execution, i}
			<ChatToolChip
				{execution}
				toolMeta={toolMetaFor(execution.tool_name, $chatModes.toolsCatalog)}
				expanded={expandedChipIndex === i}
				onToggle={() => (expandedChipIndex = expandedChipIndex === i ? null : i)}
			/>
		{/each}
	</div>
	{#if expandedError}
		<div class="mt-1 text-xs text-danger" transition:slide={{ duration: 120 }}>{expandedError}</div>
	{/if}
{/if}

{#if effectiveSteps.length > 0}
	<div class="mt-2">
		<button
			type="button"
			class="flex items-center gap-1.5 text-[11px] text-fg-subtle hover:text-fg-muted transition-colors mb-1"
			on:click={() => (userToggled = !showList)}
		>
			<svg
				class="w-3 h-3 transition-transform {showList ? 'rotate-90' : ''}"
				fill="none"
				stroke="currentColor"
				viewBox="0 0 24 24"
			>
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
			</svg>
			<span class="font-mono tabular-nums">{effectiveSteps.length} steps</span>
			{#if !showList}
				<span class="truncate max-w-[420px]">· {effectiveSteps.map(stepLabel).join(', ')}</span>
			{/if}
		</button>

		{#if showList}
			<div class="border-l-2 border-line pl-3 space-y-0.5" transition:slide={{ duration: 150 }}>
				{#each effectiveSteps as step, idx (step.seq ?? idx)}
					{@const active = activeByIndex[idx] ?? false}
					{@const seq = step.seq ?? idx}
					{@const summary = stepSummary(step)}
					<div class="min-w-0">
						<button
							type="button"
							class="w-full h-7 flex items-center gap-2 rounded px-1 -mx-1 hover:bg-surface-1 transition-colors text-left min-w-0"
							on:click={() => toggleStep(seq)}
						>
							<span class="w-3 flex items-center justify-center flex-shrink-0">
								{#if active}
									<span
										class="w-3 h-3 rounded-full border-2 border-line-strong animate-spin inline-block"
										style="border-top-color: rgb(var(--accent));"
										role="status"
										aria-label="Active"
									></span>
								{:else}
									<span class="w-1.5 h-1.5 rounded-full bg-success" title="Completed"></span>
								{/if}
							</span>
							<Icon
								name={STEP_ICON[step.step] ?? FALLBACK_STEP_ICON}
								className="w-3 h-3 flex-shrink-0 text-fg-subtle"
							/>
							<span class="text-[11px] font-medium text-fg-muted flex-shrink-0">{stepLabel(step)}</span>
							<span class="text-[10px] text-fg-subtle flex-1 truncate">{summary}</span>
							{#if step.duration_ms != null}
								<span class="font-mono tabular-nums text-2xs text-fg-disabled flex-shrink-0"
									>{step.duration_ms}ms</span
								>
							{/if}
						</button>
						{#if expandedSteps[seq] && (summary || step.step === 'loading_memory')}
							<div class="ml-5 mt-0.5 mb-1.5 min-w-0" transition:slide={{ duration: 150 }}>
								{#if step.step === 'resolving_resources' && step.detail?.uris}
									<div class="text-[10px] font-medium uppercase tracking-wider text-fg-subtle mb-1">
										Resources
									</div>
									<ul class="text-[10px] text-fg-muted font-mono space-y-0.5">
										{#each step.detail.uris as uri}
											<li class="truncate">{uri}</li>
										{/each}
									</ul>
								{:else if step.step === 'loading_memory' && step.detail?.by_scope}
									<div class="text-[10px] font-medium uppercase tracking-wider text-fg-subtle mb-1">
										By scope
									</div>
									<div class="text-[10px] text-fg-muted font-mono tabular-nums space-x-3">
										<span>global {step.detail.by_scope.global ?? 0}</span>
										<span>preset {step.detail.by_scope.preset ?? 0}</span>
										<span>model {step.detail.by_scope.model ?? 0}</span>
									</div>
								{:else if step.step === 'running_pre_chat' && step.detail?.actions}
									<div class="text-[10px] font-medium uppercase tracking-wider text-fg-subtle mb-1">
										Actions
									</div>
									<ul class="text-[10px] text-fg-muted space-y-0.5">
										{#each step.detail.actions as action}
											<li>{action}</li>
										{/each}
									</ul>
								{/if}
							</div>
						{/if}
					</div>
				{/each}
			</div>
			{#if modeLabel || hasTokenCounts}
				<div class="mt-1 pl-3 font-mono text-2xs tabular-nums text-fg-disabled">
					{#if modeLabel}{modeLabel}{/if}{#if modeLabel && hasTokenCounts} · {/if}{#if hasTokenCounts}{tokenCounts.prompt.toLocaleString()}
						prompt / {tokenCounts.completion.toLocaleString()} completion tokens{/if}
				</div>
			{/if}
			{#if contextLedger}
				<div class="mt-0.5 pl-3 font-mono text-2xs tabular-nums text-fg-disabled">
					{formatContextLedgerSummary(contextLedger)}
				</div>
			{/if}
			{#if memoryDroppedCount > 0}
				<div class="mt-0.5 pl-3 text-2xs text-fg-subtle">
					{memoryDroppedCount} note{memoryDroppedCount === 1 ? '' : 's'} over cap, not injected
				</div>
			{/if}
			{#if toolFailureEntries.length > 0}
				<div class="mt-0.5 pl-3 text-2xs text-danger">
					{toolFailureEntries.map(([name, count]) => `${name} failed ×${count}`).join(', ')}
				</div>
			{/if}
		{/if}
	</div>
{/if}
