<script lang="ts">
	import { onMount, tick, untrack } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, SegmentedControl } from '$lib/components/ui';
	import { relativeTime } from '$lib/elixirs/time';
	import { doneCount, failedCount, progressSegments, settleTier } from '$lib/elixirs/header';
	import { brewName, isPolling, runElapsed, runLabel, runTone } from '$lib/elixirs/runs';

	type Tone = 'danger' | 'signal' | 'success' | 'warning' | 'neutral';

	interface SummaryPart {
		text: string;
		tone: string;
	}

	interface RunModeItem {
		id: string;
		label: string;
	}

	interface HeaderIssue {
		code: string;
		message?: string;
		severity?: string;
		node_id?: string | null;
		edge_id?: string | null;
	}

	interface HeaderRun {
		id?: string;
		number?: number;
		status: string;
		scope?: string;
		created_at?: string | null;
		finished_at?: string | null;
	}

	interface Props {
		name?: string;
		phone?: boolean;
		runMode: string;
		runModes: RunModeItem[];
		shortRunModes: RunModeItem[];
		saveState: string;
		statusText: string;
		issues?: HeaderIssue[];
		errorCount?: number;
		setupCodes: Set<string>;
		run?: HeaderRun | null;
		runs?: HeaderRun[];
		steps?: { view?: { status?: string } | null }[];
		latestActive?: boolean;
		runBusy?: boolean;
		brewing?: boolean;
		brewBlocked?: string | null;
		stopping?: boolean;
		dockBrew?: boolean;
		runsTotal?: number;
		activeRuns?: number;
		onviewall?: () => void;
		oncommitname?: () => void;
		onrunmode?: (mode: string) => void;
		onadd?: (kind: string) => void;
		onbrew?: () => void;
		onstop?: () => void;
		onshowrun?: (runId: string) => void;
		onfocusissue?: (issue: HeaderIssue) => void;
	}

	let {
		name = $bindable(''),
		phone = false,
		runMode,
		runModes,
		shortRunModes,
		saveState,
		statusText,
		issues = [],
		errorCount = 0,
		setupCodes,
		run = null,
		runs = [],
		steps = [],
		latestActive = false,
		runBusy = false,
		brewing = false,
		brewBlocked = null,
		stopping = false,
		dockBrew = false,
		runsTotal = undefined,
		activeRuns = 0,
		onviewall,
		oncommitname,
		onrunmode,
		onadd,
		onbrew,
		onstop,
		onshowrun,
		onfocusissue
	}: Props = $props();

	const TONE_TEXT: Record<Tone, string> = { danger: 'text-danger', signal: 'text-signal', success: 'text-success', warning: 'text-warning', neutral: 'text-fg-muted' };
	const TONE_DOT: Record<Tone, string> = { danger: 'bg-danger', signal: 'bg-signal', success: 'bg-success', warning: 'bg-warning', neutral: 'bg-fg-subtle' };

	type Panel = 'problems' | 'brews' | 'more';

	let root = $state<HTMLDivElement>();
	let spacer = $state<HTMLDivElement>();
	let nameEl = $state<HTMLInputElement>();
	let right = $state<HTMLDivElement>();
	let tier = $state('wide');
	let settling = false;
	let again = false;
	let now = $state(Date.now());
	let panel = $state<Panel | null>(null);
	let moreView = $state<'main' | 'problems' | 'brews'>('main');
	let problemsTrigger = $state<HTMLButtonElement>();
	let brewsTrigger = $state<HTMLButtonElement>();
	let moreTrigger = $state<HTMLButtonElement>();

	let wide = $derived(tier === 'wide');
	let minimal = $derived(tier === 'minimal');
	let foldedAdds = $derived(tier === 'compact' || minimal);
	let tone = $derived(runTone(run));
	let done = $derived(doneCount(steps));
	let failed = $derived(failedCount(steps));
	let segments = $derived<SummaryPart[]>(run ? progressSegments(brewName(runs, run.id ?? ''), done, steps.length, failed, runElapsed(run, now), tier) : []);
	let fullSegments = $derived<SummaryPart[]>(run ? progressSegments(brewName(runs, run.id ?? ''), done, steps.length, failed, runElapsed(run, now), 'wide') : []);
	const RECENT = 5;
	let recent = $derived(runs.slice(0, RECENT));
	let brewTotal = $derived(runsTotal ?? runs.length);
	let primaryLabel = $derived(runs.length > 0 ? 'Brew again' : 'Brew');
	let problemWord = $derived(issues.length === 1 ? 'problem' : 'problems');
	let saveDot = $derived(saveState === 'saved' ? null : saveState === 'error' || saveState === 'conflict' ? 'bg-danger' : 'bg-warning');

	$effect(() => {
		if (!isPolling(run)) return;
		now = Date.now();
		const timer = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(timer);
	});

	function natural(): number {
		if (!root || !spacer || !nameEl) return 0;
		const nameFloor = parseFloat(getComputedStyle(nameEl).minWidth) || 0;
		const slack = spacer.offsetWidth + Math.max(0, nameEl.offsetWidth - nameFloor);
		return root.scrollWidth - slack;
	}

	async function refit() {
		if (!root) return;
		if (settling) {
			again = true;
			return;
		}
		settling = true;
		try {
			let passes = 0;
			do {
				again = false;
				passes += 1;
				if (phone) {
					tier = 'minimal';
					await tick();
					continue;
				}
				await settleTier({
					start: tier,
					available: root.clientWidth,
					apply: async (next: string) => {
						tier = next;
						await tick();
					},
					measure: natural
				});
			} while (again && passes < 3);
		} finally {
			settling = false;
		}
	}

	$effect(() => {
		void now;
		void done;
		void failed;
		void steps.length;
		void run?.id;
		void statusText;
		void primaryLabel;
		void problemWord;
		void latestActive;
		void run?.status;
		void runs.length;
		void issues.length;
		void phone;
		untrack(refit);
	});

	onMount(() => {
		const observer = new ResizeObserver(() => refit());
		if (root) observer.observe(root);
		if (spacer) observer.observe(spacer);
		if (right) observer.observe(right);
		refit();
		return () => observer.disconnect();
	});

	function toggle(next: Panel) {
		panel = panel === next ? null : next;
		moreView = 'main';
	}

	function close(restore = true) {
		const was = panel;
		panel = null;
		moreView = 'main';
		if (!restore || !was) return;
		const trigger = { problems: problemsTrigger, brews: brewsTrigger, more: moreTrigger }[was];
		trigger?.focus();
	}

	function onkeydown(event: KeyboardEvent) {
		if (!panel || event.key !== 'Escape') return;
		event.preventDefault();
		if (panel === 'more' && moreView !== 'main') {
			moreView = 'main';
			return;
		}
		close();
	}

	function onpointerdown(event: PointerEvent) {
		if (!panel) return;
		if (event.target instanceof Element && event.target.closest('[data-header-panel]')) return;
		close(false);
	}

	function pickIssue(issue: HeaderIssue) {
		close(false);
		onfocusissue?.(issue);
	}

	function viewAll() {
		close(false);
		onviewall?.();
	}

	function pickRun(runId: string) {
		close();
		onshowrun?.(runId);
	}

	function pickMode(next: string) {
		onrunmode?.(next);
	}

	function pickAdd(kind: string) {
		close(false);
		onadd?.(kind);
	}
</script>

<svelte:window {onkeydown} {onpointerdown} />

{#snippet problemsList()}
	{#if issues.length === 0}
		<p class="px-2 py-1.5 text-sm text-fg-muted">Nothing to fix. Every node has what it needs.</p>
	{:else}
		<ul class="flex flex-col gap-1">
			{#each issues as issue, index (index)}
				<li>
					<button
						type="button"
						class="w-full rounded px-2 py-1.5 text-left text-sm hover:bg-surface-2 {setupCodes.has(issue.code) ? 'text-fg-muted' : issue.severity === 'error' ? 'text-danger' : 'text-warning'}"
						onclick={() => pickIssue(issue)}
					>
						{issue.message}
					</button>
				</li>
			{/each}
		</ul>
	{/if}
{/snippet}

{#snippet summary(parts: SummaryPart[])}
	{#each parts as part, index (index)}
		{#if index > 0}<span class="px-1 text-fg-subtle" aria-hidden="true">·</span>{/if}<span class={part.tone === 'danger' ? 'text-danger' : ''}>{part.text}</span>
	{/each}
{/snippet}

{#snippet brewsList(max: string)}
	<p class="px-2 pb-1.5 text-sm text-fg-subtle">Latest brews. Each keeps its own outputs.</p>
	<ul class="flex {max} flex-col gap-0.5 overflow-y-auto">
		{#each recent as item (item.id)}
			<li>
				<button
					type="button"
					class="flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-surface-2 {item.id === run?.id ? 'bg-signal/10' : ''}"
					aria-current={item.id === run?.id ? 'true' : undefined}
					onclick={() => pickRun(item.id ?? '')}
				>
					<span class="min-w-0 truncate {item.id === run?.id ? 'text-signal' : 'text-fg'}">{brewName(runs, item.id ?? '')}</span>
					<span class="shrink-0 {TONE_TEXT[runTone(item) as Tone]}">{runLabel(item)}</span>
					<span class="shrink-0 text-fg-subtle">{relativeTime(item.created_at)}</span>
				</button>
			</li>
		{/each}
	</ul>
	<div class="my-1 h-px bg-line" role="separator"></div>
	<button type="button" class="menu-item" data-testid="view-all-brews" onclick={viewAll}>
		<Icon name={'list'} className="h-4 w-4 text-fg-muted" />
		<span class="flex-1">{brewTotal > RECENT ? `View all ${brewTotal} brews` : 'Brew history'}</span>
		{#if activeRuns > 0}<span class="font-mono text-sm tabular-nums text-signal">{activeRuns} active</span>{/if}
	</button>
{/snippet}

<div
	bind:this={root}
	class="elixir-header relative flex min-w-0 flex-shrink-0 items-center gap-2 border-b border-line bg-surface-1 px-3 py-2 sm:px-4"
	data-panel-open={panel ? '' : undefined}
>
	<label class="sr-only" for="elixir-name">Elixir name</label>
	<input
		id="elixir-name"
		bind:this={nameEl}
		class="input name-input text-md font-semibold {minimal ? 'compact-name' : ''}"
		maxlength="120"
		bind:value={name}
		onblur={oncommitname}
		onkeydown={(event) => event.key === 'Enter' && event.currentTarget.blur()}
	/>
	{#if !foldedAdds}
		<div class="shrink-0">
			<SegmentedControl
				items={wide ? runModes : shortRunModes}
				selected={runMode}
				onSelect={pickMode}
				variant="toggle"
				ariaLabel="How a brew continues between presets"
			/>
		</div>
	{/if}

	<div bind:this={spacer} class="min-w-0 flex-1"></div>

	<div bind:this={right} class="flex shrink-0 items-center gap-2">
		<span class="inline-flex items-center gap-1.5 text-sm {saveState === 'error' || saveState === 'conflict' ? 'text-danger' : 'text-fg-muted'} {wide ? '' : 'sr-only'}" role="status">
			{statusText}
		</span>
		{#if !wide && saveDot}
			<span class="h-2 w-2 shrink-0 rounded-full {saveDot}" aria-hidden="true"></span>
		{/if}

		{#if !minimal}
			<div class="relative" data-header-panel>
				<button
					bind:this={problemsTrigger}
					type="button"
					class="problems-button {errorCount > 0 ? 'has-errors' : ''}"
					aria-label="{issues.length} {problemWord}"
					aria-expanded={panel === 'problems'}
					aria-controls="elixir-problems"
					onclick={() => toggle('problems')}
				>
					<Icon name={errorCount > 0 ? 'warning' : 'check-circle'} className="h-4 w-4" />
					<span class="font-mono tabular-nums">{issues.length}</span>
					{#if tier === 'wide' || tier === 'mid'}
						<span>{problemWord}</span>
					{/if}
				</button>
				{#if panel === 'problems'}
					<div id="elixir-problems" class="panel right-0 w-[min(22rem,calc(100vw-2rem))] max-h-[60dvh] overflow-y-auto">
						{@render problemsList()}
					</div>
				{/if}
			</div>
		{/if}

		{#if run && !minimal}
			<span class="inline-flex items-center gap-1.5 rounded border border-line px-2 py-1 text-sm {TONE_TEXT[tone as Tone]}" role="status" aria-label="Brew status">
				<span class="h-2 w-2 rounded-full {TONE_DOT[tone as Tone]}" aria-hidden="true"></span>
				{runLabel(run)}
			</span>
			<p class="whitespace-nowrap font-mono text-sm tabular-nums text-fg-muted" role="group" aria-label="Brew progress">
				{@render summary(segments)}
			</p>
		{/if}

		{#if !foldedAdds}
			<Tooltip text="Add a preset node">
				{#if wide}
					<Button variant="secondary" size="sm" icon="plus" onclick={() => onadd?.('preset')}>Preset</Button>
				{:else}
					<button type="button" class="header-icon bordered" aria-label="Preset" onclick={() => onadd?.('preset')}>
						<Icon name={'plus'} className="h-4 w-4" />
					</button>
				{/if}
			</Tooltip>
			<Tooltip text="Add a media source">
				{#if wide}
					<Button variant="secondary" size="sm" icon="image" onclick={() => onadd?.('media')}>Media</Button>
				{:else}
					<button type="button" class="header-icon bordered" aria-label="Media" onclick={() => onadd?.('media')}>
						<Icon name={'image'} className="h-4 w-4" />
					</button>
				{/if}
			</Tooltip>
			<Tooltip text="Add a prompt writer">
				{#if wide}
					<Button variant="secondary" size="sm" icon="sparkles" onclick={() => onadd?.('llm')}>Writer</Button>
				{:else}
					<button type="button" class="header-icon bordered" aria-label="Prompt writer" onclick={() => onadd?.('llm')}>
						<Icon name={'sparkles'} className="h-4 w-4" />
					</button>
				{/if}
			</Tooltip>
		{/if}

		{#if runs.length > 0 && !minimal}
			<div class="relative" data-header-panel>
				<button
					bind:this={brewsTrigger}
					type="button"
					class="header-chip"
					aria-label="Brews {brewTotal}{activeRuns > 0 ? `, ${activeRuns} active` : ''}"
					aria-expanded={panel === 'brews'}
					aria-controls="elixir-brews"
					onclick={() => toggle('brews')}
				>
					<Icon name={'clock'} className="h-4 w-4" />
					{#if tier !== 'compact'}
						<span>Brews</span>
					{/if}
					<span class="font-mono tabular-nums text-fg-subtle">{brewTotal}</span>
					{#if activeRuns > 0}
						<span class="live" data-testid="brews-live"><span class="live-dot" aria-hidden="true"></span><span class="font-mono tabular-nums">{activeRuns}</span>{#if wide}<span>active</span>{/if}</span>
					{/if}
				</button>
				{#if panel === 'brews'}
					<div id="elixir-brews" class="panel right-0 w-[min(20rem,calc(100vw-2rem))]">
						{@render brewsList('max-h-[50dvh]')}
					</div>
				{/if}
			</div>
		{/if}

		{#if run && minimal}
			<span class="inline-flex shrink-0 items-center gap-1.5" role="status" aria-label="Brew status">
				<span class="h-2.5 w-2.5 rounded-full {TONE_DOT[tone as Tone]}" class:pulse={latestActive} aria-hidden="true"></span>
				<span class="sr-only">{runLabel(run)}</span>
				{#if steps.length > 0}
					<span class="whitespace-nowrap font-mono text-xs tabular-nums text-fg-muted" data-testid="elixir-mini-summary">{done}/{steps.length}{runElapsed(run, now) ? ` ${runElapsed(run, now)}` : ''}</span>
				{/if}
			</span>
		{/if}

		{#if !dockBrew && latestActive}
			<Button variant="danger" size="sm" icon="close" loading={runBusy} disabled={stopping} onclick={onstop}>Stop</Button>
		{:else if !dockBrew}
			<Tooltip text={brewBlocked ?? 'Run every node, following the connections'}>
				<Button variant="primary" size="sm" icon="play" loading={brewing} disabled={!!brewBlocked} onclick={onbrew}>{primaryLabel}</Button>
			</Tooltip>
		{/if}

		{#if foldedAdds}
			<div class="relative" data-header-panel>
				<button
					bind:this={moreTrigger}
					type="button"
					class="header-icon relative"
					aria-label="More"
					aria-haspopup="menu"
					aria-expanded={panel === 'more'}
					onclick={() => toggle('more')}
				>
					<Icon name={'more'} className="h-4 w-4" />
					{#if minimal && errorCount > 0}
						<span class="absolute right-1 top-1 h-2 w-2 rounded-full bg-danger" aria-hidden="true"></span>
					{/if}
				</button>
				{#if panel === 'more'}
					<div class="panel right-0 w-[min(20rem,calc(100vw-2rem))] max-h-[70dvh] overflow-y-auto" role="menu" aria-label="More">
						{#if moreView === 'problems'}
							<button type="button" class="menu-item text-fg-muted" onclick={() => (moreView = 'main')}>
								<Icon name={'arrow-left'} className="h-4 w-4" />
								Back
							</button>
							{@render problemsList()}
						{:else if moreView === 'brews'}
							<button type="button" class="menu-item text-fg-muted" onclick={() => (moreView = 'main')}>
								<Icon name={'arrow-left'} className="h-4 w-4" />
								Back
							</button>
							<div id="elixir-brews">
								{@render brewsList('max-h-[50dvh]')}
							</div>
						{:else}
							{#if run && minimal}
								<p class="whitespace-nowrap px-2 pb-1 pt-1 font-mono text-sm tabular-nums text-fg-muted" role="group" aria-label="Brew progress">
									<span class="{TONE_TEXT[tone as Tone]}">{runLabel(run)}</span>
									<span class="px-1 text-fg-subtle" aria-hidden="true">·</span>{@render summary(fullSegments)}
								</p>
								<div class="my-1 h-px bg-line" role="separator"></div>
							{/if}
							<p class="px-2 pb-1 pt-1 text-sm text-fg-subtle">How a brew continues</p>
							{#each runModes as mode (mode.id)}
								<button type="button" class="menu-item" role="menuitemradio" aria-checked={runMode === mode.id} onclick={() => pickMode(mode.id)}>
									<span class="h-2 w-2 shrink-0 rounded-full {runMode === mode.id ? 'bg-signal' : 'border border-line-strong'}" aria-hidden="true"></span>
									{mode.label}
								</button>
							{/each}
							<div class="my-1 h-px bg-line" role="separator"></div>
							<button type="button" class="menu-item" role="menuitem" onclick={() => pickAdd('preset')}>
								<Icon name={'plus'} className="h-4 w-4" />
								Add a preset node
							</button>
							<button type="button" class="menu-item" role="menuitem" onclick={() => pickAdd('media')}>
								<Icon name={'image'} className="h-4 w-4" />
								Add a media source
							</button>
							<button type="button" class="menu-item" role="menuitem" onclick={() => pickAdd('llm')}>
								<Icon name={'sparkles'} className="h-4 w-4" />
								Add a prompt writer
							</button>
							{#if minimal}
								<div class="my-1 h-px bg-line" role="separator"></div>
								<button type="button" class="menu-item" role="menuitem" onclick={() => (moreView = 'problems')}>
									<Icon name={errorCount > 0 ? 'warning' : 'check-circle'} className="h-4 w-4" />
									<span class="flex-1 {errorCount > 0 ? 'text-danger' : ''}">
										<span class="font-mono tabular-nums">{issues.length}</span>
										{problemWord}
									</span>
								</button>
								{#if runs.length > 0}
									<button type="button" class="menu-item" role="menuitem" onclick={() => (moreView = 'brews')}>
										<Icon name={'clock'} className="h-4 w-4" />
										<span class="flex-1">Brews</span>
										{#if activeRuns > 0}<span class="font-mono tabular-nums text-signal">{activeRuns} active</span>{/if}
										<span class="font-mono tabular-nums text-fg-subtle">{brewTotal}</span>
									</button>
								{/if}
							{/if}
						{/if}
					</div>
				{/if}
			</div>
		{/if}
	</div>
</div>

<style>
	.pulse {
		animation: pulse 1.6s ease-in-out infinite;
	}
	@keyframes pulse {
		50% {
			opacity: 0.35;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.pulse {
			animation: none;
		}
	}
	.name-input {
		flex: 0 1 14rem;
		min-width: 10rem;
		text-overflow: ellipsis;
	}
	.name-input.compact-name {
		flex: 1 1 0;
		min-width: 7.5rem;
	}
	.header-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
		min-width: 2rem;
		min-height: 2rem;
		border-radius: 4px;
		color: rgb(var(--fg-muted));
	}
	.header-icon:hover {
		background: rgb(var(--surface-3));
		color: rgb(var(--fg));
	}
	.header-icon.bordered {
		border: 1px solid rgb(var(--line));
	}
	.header-icon.bordered:hover {
		border-color: rgb(var(--line-hover));
	}
	.header-chip,
	.problems-button {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		min-height: 2rem;
		padding: 0 0.625rem;
		border-radius: 4px;
		border: 1px solid rgb(var(--line));
		font-size: 12px;
		color: rgb(var(--fg-muted));
	}
	.live {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		padding-left: 6px;
		margin-left: 2px;
		border-left: 1px solid rgb(var(--line));
		color: rgb(var(--signal));
	}
	.live-dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: rgb(var(--signal));
		animation: pulse 1.6s ease-in-out infinite;
	}
	.header-chip:hover,
	.problems-button:hover {
		border-color: rgb(var(--line-hover));
		color: rgb(var(--fg));
	}
	.problems-button.has-errors {
		color: rgb(var(--danger));
		border-color: rgb(var(--danger) / 0.4);
	}
	.panel {
		position: absolute;
		top: 100%;
		z-index: 40;
		margin-top: 0.25rem;
		padding: 0.5rem;
		border-radius: 10px;
		border: 1px solid rgb(var(--line));
		background: rgb(var(--surface-1));
		box-shadow: var(--shadow-floating, 0 8px 24px rgb(0 0 0 / 0.3));
	}
	.menu-item {
		display: flex;
		width: 100%;
		align-items: center;
		gap: 0.5rem;
		min-height: 2.25rem;
		padding: 0 0.5rem;
		border-radius: 4px;
		text-align: left;
		font-size: 14px;
		color: rgb(var(--fg));
	}
	.menu-item:hover {
		background: rgb(var(--surface-2));
	}
</style>
