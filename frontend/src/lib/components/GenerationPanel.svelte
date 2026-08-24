<script lang="ts">
	import { onMount, onDestroy, createEventDispatcher } from 'svelte';
	import { browser } from '$app/environment';
	import { storage } from '$lib/utils/storage';
	import type { GenerationState } from '$lib/types/tabs';
	import type { PresetModeVariant } from '$lib/types/api';
	import { parseTemplateMarkers } from '$lib/utils/templateProcessor';
	import { contributionsForSlot } from '$lib/extensions/extensionSlots';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import Button from './ui/Button.svelte';
	import Icon from './Icon.svelte';
	import Tooltip from './Tooltip.svelte';
	import GenerateMark from './generation-panel/GenerateMark.svelte';
	import ReadoutCell from './generation-panel/ReadoutCell.svelte';
	import SessionCluster from './generation-panel/SessionCluster.svelte';
	import { deriveMarkState, deriveModeChromeGlyph, formatDurationMs, formatDurationSeconds } from './generation-panel/barState';
	import { shortcutLabels } from '$lib/stores/keybindings';
	import { createGenerationModeController } from './generationModeController';

	const dispatch = createEventDispatcher();

	// `generation.panel.modes` extension slot: plugin-contributed extra drawer
	// tabs alongside the core settings drawer, each identified
	// by a synthetic `plugin:<pluginId>:<component>` drawer id.
	$: panelModeContributions = contributionsForSlot('generation.panel.modes');
	const pluginDrawerId = (c: { plugin_id: string; component: string }) => `plugin:${c.plugin_id}:${c.component}`;
	$: activePanelModeContribution = $panelModeContributions.find((c) => pluginDrawerId(c) === activeDrawer);

	// Props
	export let generation: GenerationState;
	export let isGenerating: boolean = false;
	export let onGenerate: (() => void) | undefined = undefined;
	export let onCancel: (() => void) | undefined = undefined;
	export let canGenerate: boolean = false;
	// First reason `canGenerate` is false (from the caller's own gate, e.g. a
	// Video Director validation reason). Only ever shown while the mark is
	// actually `disabled` — never overrides the running/armed/ready text.
	export let disabledReason: string | undefined = undefined;
	export let generatingTabName: string | undefined = undefined;
	export let isActiveTabGenerating: boolean = false;
	export let onSwitchToGeneratingTab: (() => void) | undefined = undefined;
	export let onClearQueue: (() => void) | undefined = undefined;
	// Forwarded to the session cluster.
	export let presetId: string | null = null;
	export let currentMode: string | null = null;
	// No default-empty-string guard needed downstream: SessionCluster only
	// starts fetching sessions once `presetId` is set, and an empty tabId
	// just never matches a tab in tabsStore (inert, not an error) — safe for
	// callers (e.g. the docs component gallery) that mount this panel without
	// a real tab.
	export let tabId: string = '';
	export let presetVersion: string | undefined = undefined;
	export let availableModes: Array<{ id: string; variants?: PresetModeVariant[] }> = [];

	// Backend generation queue: everything this tab has enqueued (pending or running).
	$: queueEntries = generation.queue || [];
	$: queueDepth = queueEntries.length;

	// Local state
	let mounted = false;
	let mainElement: HTMLElement | null = null;

	// Drawer state - single state to manage the settings drawer, the
	// last-generations drawer, plus any `plugin:<pluginId>:<component>`
	// drawer from a `generation.panel.modes` contribution. The run-report
	// history drawer moved to the admin Generations page; `lastGenerations`
	// is a separate, simpler user-facing recent-results drawer.
	type DrawerType = 'settings' | 'lastGenerations' | string | null;
	let activeDrawer: DrawerType = null;
	const STORAGE_KEY_DRAWER = 'generation-panel-active-drawer';

	// Toggle drawer open/closed
	function toggleDrawer(drawer: DrawerType) {
		if (activeDrawer === drawer) {
			activeDrawer = null;
		} else {
			activeDrawer = drawer;
		}
		if (activeDrawer) {
			storage.set(STORAGE_KEY_DRAWER, activeDrawer);
		} else {
			storage.remove(STORAGE_KEY_DRAWER);
		}
	}

	// Close drawer
	function closeDrawer() {
		activeDrawer = null;
		storage.remove(STORAGE_KEY_DRAWER);
	}

	// Generation mode state
	const modeController = createGenerationModeController(() => handleGenerate());
	const { mode: generationMode, stopAfterCurrentRequested } = modeController;
	let showQueuePopover = false;
	let queuePopoverRef: HTMLDivElement;
	let queueCellRef: HTMLDivElement;

	// The mark's visual state, and the mode chrome slot's glyph, are pure
	// derivations (see generation-panel/barState.ts) — kept out of components
	// so they're unit-testable without mounting Svelte.
	$: markState = deriveMarkState({ isGenerating, canGenerate: mounted && canGenerate, mode: $generationMode });
	$: modeChromeGlyph = deriveModeChromeGlyph({
		isGenerating,
		mode: $generationMode,
		stopAfterCurrentRequested: $stopAfterCurrentRequested
	});
	$: modeChromeIcon = modeChromeGlyph === 'pause' ? 'pause' : modeChromeGlyph === 'stopping' ? 'hourglass' : 'refresh';
	$: modeChromeActive = modeChromeGlyph === 'idle' && $generationMode === 'forever';
	$: modeChromeDisabled = modeChromeGlyph === 'stopping' || (modeChromeGlyph === 'idle' && !(mounted && canGenerate));
	$: modeChromeTooltip =
		modeChromeGlyph === 'stopping'
			? 'Finishing this generation, then stopping continuous mode'
			: modeChromeGlyph === 'pause'
				? 'Stop after current generation'
				: $generationMode === 'forever'
					? 'Mode: Continuous'
					: 'Mode: Generate once';

	function handleModeChromeClick() {
		if (modeChromeGlyph === 'stopping') return;
		if (modeChromeGlyph === 'pause') {
			handleStopAfterCurrent();
			return;
		}
		modeController.setMode($generationMode === 'forever' ? 'once' : 'forever');
	}

	function handleMarkClick() {
		if (markState === 'running') {
			handleCancel();
		} else if (markState !== 'disabled') {
			handleGenerate();
		}
	}

	$: markLabel =
		markState === 'running'
			? 'Cancel generation'
			: markState === 'disabled' && disabledReason
				? disabledReason
				: $generationMode === 'forever'
					? 'Start continuous generation'
					: 'Generate';

	// Timer state
	let generationStartTime: number | null = null;
	let generationEndTime: number | null = null;
	let timerInterval: ReturnType<typeof setInterval> | null = null;
	let elapsedSeconds = 0;

	onMount(() => {
		mounted = true;
		mainElement = document.querySelector('main');
		// Restore drawer state from localStorage
		const savedDrawer = storage.get(STORAGE_KEY_DRAWER);
		if (savedDrawer === 'settings' || savedDrawer === 'lastGenerations') {
			activeDrawer = savedDrawer;
		}
	});

	// Lock page scroll when drawer is open
	$: if (browser) {
		if (activeDrawer) {
			document.documentElement.style.overflow = 'hidden';
			document.body.style.overflow = 'hidden';
			if (mainElement) {
				mainElement.style.overflow = 'hidden';
			}
		} else {
			document.documentElement.style.overflow = '';
			document.body.style.overflow = '';
			if (mainElement) {
				mainElement.style.overflow = '';
			}
		}
	}

	// Cleanup on destroy
	onDestroy(() => {
		if (browser) {
			document.documentElement.style.overflow = '';
			document.body.style.overflow = '';
			if (mainElement) {
				mainElement.style.overflow = '';
			}
		}
	});

	// Use afterUpdate to detect generation state changes
	let wasGenerating = false;

	$: {
		// Generation started
		if (!wasGenerating && isGenerating) {
			generationStartTime = generation.startedAt ?? Date.now();
			generationEndTime = null;
			elapsedSeconds = Math.max(0, (Date.now() - generationStartTime) / 1000);

			// Start timer interval
			if (timerInterval) clearInterval(timerInterval);
			timerInterval = setInterval(() => {
				if (generationStartTime) {
					elapsedSeconds = (Date.now() - generationStartTime) / 1000;
				}
			}, 500); // Update every 500ms — sub-second precision is unnecessary for display

			wasGenerating = true;
		}

		// Generation completed
		if (wasGenerating && !isGenerating) {
			generationEndTime = Date.now();
			if (timerInterval) {
				clearInterval(timerInterval);
				timerInterval = null;
			}
			if (generationStartTime && generationEndTime) {
				elapsedSeconds = (generationEndTime - generationStartTime) / 1000;
			}
			dispatch('generationcomplete');
			modeController.handleGenerationComplete();

			wasGenerating = false;
		}
	}

	function handleGenerate() {
		activeDrawer = null; // Close drawer when starting generation

		// Reset timer state
		generationStartTime = null;
		generationEndTime = null;
		elapsedSeconds = 0;

		modeController.handleGenerationStart();

		// Dispatch event before calling onGenerate
		dispatch('generationstart');

		onGenerate?.();
	}

	// Handle cancel button click
	function handleCancel() {
		modeController.cancel();
		onCancel?.();
	}

	// Handle "Stop after current generation" click: lets the in-flight
	// generation finish normally, just drops the queued continuation.
	function handleStopAfterCurrent() {
		modeController.requestStopAfterCurrent();
	}

	// Click outside handler for the queue popover
	function handleClickOutside(event: MouseEvent) {
		const target = event.target as HTMLElement;

		if (showQueuePopover &&
			queuePopoverRef &&
			!queuePopoverRef.contains(target) &&
			queueCellRef &&
			!queueCellRef.contains(target)) {
			showQueuePopover = false;
		}
	}

	onMount(() => {
		document.addEventListener('mousedown', handleClickOutside);
		return () => {
			document.removeEventListener('mousedown', handleClickOutside);
		};
	});

	onDestroy(() => {
		// Clean up timer interval
		if (timerInterval) {
			clearInterval(timerInterval);
			timerInterval = null;
		}
		modeController.dispose();
	});

	// Get progress percentage; null means the active stage reported no fraction
	// yet (e.g. a cold model load) and the bar renders indeterminate instead.
	$: hasProgressFraction = generation.currentProgress?.progress != null;
	$: progressPercent = Math.round((generation.currentProgress?.progress ?? 0) * 100);
	$: stepParsed = parseTemplateMarkers(generation.currentProgress?.current_step || '');
	$: messageParsed = parseTemplateMarkers(generation.currentProgress?.message || '');
	$: progressMarkers = [...stepParsed.markers, ...messageParsed.markers];
	$: currentPipeName = progressMarkers.find((marker) => marker.type === 'PIPE')?.value;
	// Deduped by type+value: current_step and message can repeat a marker, and
	// the keyed each below uses type+value as its key.
	$: progressMeta = progressMarkers.filter(
		(marker, i, arr) =>
			marker.type !== 'PIPE' &&
			marker.type !== 'PROGRESS' &&
			arr.findIndex((m) => m.type === marker.type && m.value === marker.value) === i
	);
	$: progressMessage = [stepParsed.plain, messageParsed.plain].filter(Boolean).join(' — ');

	// The `elapsed`/`last` readout cells: elapsed only exists while this tab
	// is actively generating, last persists across the next run (see
	// GenerationState.lastDurationMs).
	$: elapsedText = isGenerating ? formatDurationSeconds(elapsedSeconds) : 'none';
	$: lastText = formatDurationMs(generation.lastDurationMs ?? null);
	$: queueText = queueDepth > 0 ? `${queueDepth} ${queueDepth === 1 ? 'job' : 'jobs'}` : 'empty';
</script>

<!-- Backdrop for drawer -->
{#if activeDrawer}
	<div
	class="fixed top-0 left-0 right-0 bottom-[73px] bg-canvas/50 backdrop-blur-sm z-40 transition-opacity duration-300"
		on:click={closeDrawer}
		on:keydown={(e) => e.key === 'Escape' && closeDrawer()}
		role="button"
		tabindex="-1"
		aria-label="Close drawer"
	></div>
{/if}

<!-- Right Slide-Out Drawer -->
<div
	class="fixed top-0 right-0 bottom-[73px] {activeDrawer === 'settings' ? 'w-[380px]' : activeDrawer === 'lastGenerations' ? 'w-[480px]' : 'w-[1000px]'} max-w-[90vw] bg-surface-1 border-l border-line/50 z-50 shadow-overlay
		transform transition-all duration-300 ease-out
		{activeDrawer ? 'translate-x-0 opacity-100' : 'translate-x-full opacity-0'}"
>
	<!-- Drawer Header -->
	<div class="flex items-center justify-between p-4 border-b border-line-strong/50 bg-surface-2/50 backdrop-blur-sm">
		<h2 class="font-semibold text-fg">
			{#if activeDrawer === 'settings'}
				Generation Settings
			{:else if activeDrawer === 'lastGenerations'}
				Last Generations
			{:else if activePanelModeContribution}
				{activePanelModeContribution.label || activePanelModeContribution.component}
			{/if}
		</h2>
		<Tooltip text="Close drawer" position="left" delay={150}>
			<button
				type="button"
				class="p-2 text-fg-muted hover:text-fg hover:bg-surface-3 rounded-lg transition-colors"
				on:click={closeDrawer}
				aria-label="Close drawer"
			>
				<Icon name="close" className="w-5 h-5" />
			</button>
		</Tooltip>
	</div>

	<!-- Drawer Content -->
	<!-- Settings content (app-level generation options, provided by the page) -->
	<div class="{activeDrawer === 'settings' ? 'overflow-y-auto h-[calc(100%-57px)]' : 'hidden'}">
		<slot name="settings" />
	</div>

	<!-- Last-generations content (recent results for this tab's preset,
		provided by the page). Mounted only while open, like the plugin
		drawers below, so it fetches fresh every time it's opened. -->
	{#if activeDrawer === 'lastGenerations'}
		<div class="overflow-y-auto h-[calc(100%-57px)]">
			<slot name="lastGenerations" />
		</div>
	{/if}

	<!-- Plugin `generation.panel.modes` content -->
	{#each $panelModeContributions as modeContrib (pluginDrawerId(modeContrib))}
		<div class="{activeDrawer === pluginDrawerId(modeContrib) ? 'overflow-y-auto h-[calc(100%-57px)]' : 'hidden'}">
			{#if activeDrawer === pluginDrawerId(modeContrib)}
				{#await resolvePluginComponent(modeContrib.plugin_id, modeContrib.component) then Component}
					{#if Component}
						<svelte:component this={Component} {generation} />
					{/if}
				{/await}
			{/if}
		</div>
	{/each}
</div>

<!-- Generation console bar -->
<div class="relative w-full border-t border-line-strong bg-surface-1">
	<div
		class="absolute inset-x-0 top-0 h-1 overflow-hidden bg-surface-3"
		role="progressbar"
		aria-label="Generation progress"
		aria-valuemin="0"
		aria-valuemax="100"
		aria-valuenow={isGenerating && hasProgressFraction ? progressPercent : undefined}
	>
		{#if isGenerating}
			{#if hasProgressFraction}
				<div
					class="h-full bg-signal"
					style="width: {progressPercent}%; transition: width 150ms var(--ease-out-quart);"
				></div>
			{:else}
				<div class="h-full w-1/3 bg-signal progress-indeterminate"></div>
			{/if}
		{/if}
	</div>

	<div class="mx-auto flex h-[72px] w-full max-w-[1800px] items-center gap-3 px-4 pt-1 lg:px-6">
		<!-- Status block -->
		<div class="flex min-w-0 flex-1 items-center gap-3" aria-live="polite">
			<span class="h-2 w-2 flex-shrink-0 rounded-full {isGenerating ? "bg-accent animate-pulse" : generatingTabName && !isActiveTabGenerating ? "bg-warning" : "bg-success"}"></span>
			<div class="min-w-0">
				{#if isGenerating}
					<div class="flex min-w-0 items-center gap-2">
						<span class="text-sm font-semibold text-fg">Running</span>
						{#if progressMessage}<span class="truncate text-sm text-fg-muted">{progressMessage}</span>{/if}
					</div>
					<div class="flex items-center gap-2 overflow-hidden font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
						{#if currentPipeName}<span class="truncate">{currentPipeName}</span>{/if}
						{#each progressMeta as marker (marker.type + marker.value)}
							<span class="hidden truncate xl:inline">{marker.value}</span>
						{/each}
						{#if $stopAfterCurrentRequested}
							<span class="text-warning normal-case tracking-normal">stopping after this one</span>
						{:else if $generationMode === "forever"}
							<span class="text-signal normal-case tracking-normal">continuous</span>
						{/if}
						<span class="text-fg-muted">{hasProgressFraction ? `${progressPercent}%` : "working…"}</span>
					</div>
				{:else if generatingTabName && !isActiveTabGenerating}
					<div class="truncate text-sm font-medium text-fg">Generating in {generatingTabName}</div>
					<button type="button" class="text-xs text-signal hover:underline" on:click={onSwitchToGeneratingTab}>Switch to tab</button>
				{:else}
					<div class="text-sm font-medium text-fg">{markState === "disabled" && disabledReason ? "Can't generate yet" : "Ready to generate"}</div>
					<div class="truncate font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
						{markState === "disabled" && disabledReason
							? disabledReason
							: $generationMode === "forever" ? "Continuous mode armed" : "Configure the prompt and settings"}
					</div>
				{/if}
			</div>
		</div>

		<!-- Readout cluster: session · save · last · elapsed · queue, then chrome
			(settings · mode), then the mark. Fixed order, every cell
			always rendered (generation-panel.dc.html line 415) — a value that
			doesn't exist yet reads "none"/"empty" rather than the cell vanishing. -->
		<div class="flex h-[38px] flex-shrink-0 items-stretch">
			<SessionCluster {presetId} {currentMode} {tabId} {presetVersion} {availableModes} />

			<span class="h-[26px] w-px flex-shrink-0 self-center bg-line" aria-hidden="true"></span>
			<ReadoutCell label="last">
				<span class={lastText === "none" ? "text-fg-subtle" : "text-fg-muted"}>{lastText}</span>
			</ReadoutCell>

			<span class="h-[26px] w-px flex-shrink-0 self-center bg-line" aria-hidden="true"></span>
			<ReadoutCell label="elapsed">
				<span class={isGenerating ? "text-fg" : "text-fg-subtle"}>{elapsedText}</span>
			</ReadoutCell>

			<span class="h-[26px] w-px flex-shrink-0 self-center bg-line" aria-hidden="true"></span>
			<div class="relative" bind:this={queueCellRef}>
				<ReadoutCell
					label="queue"
					clickable={queueDepth > 0}
					onclick={() => (showQueuePopover = !showQueuePopover)}
				>
					<span class={queueDepth > 0 ? "text-fg-muted" : "text-fg-subtle"}>{queueText}</span>
				</ReadoutCell>
				{#if showQueuePopover}
					<div bind:this={queuePopoverRef} class="absolute bottom-full right-0 z-50 mb-2 w-72 overflow-hidden rounded-xl border border-line-strong bg-surface-1 shadow-floating" role="menu">
						<div class="flex items-center justify-between border-b border-line px-4 py-3">
							<div><p class="text-sm font-medium text-fg">Generation queue</p><p class="text-xs text-fg-subtle">Jobs from this tab</p></div>
							<Button variant="ghost" size="xs" onclick={() => { showQueuePopover = false; onClearQueue?.(); }}>Cancel all</Button>
						</div>
						<div class="max-h-64 overflow-y-auto p-2">
							{#each queueEntries as entry, index (entry.generation_id)}
								<div class="flex items-center gap-3 rounded-lg px-2 py-2">
									<span class="flex h-6 w-6 items-center justify-center rounded bg-surface-2 font-mono text-2xs text-fg-subtle">{index + 1}</span>
									<div class="min-w-0 flex-1"><p class="truncate font-mono text-xs text-fg-muted">{entry.generation_id}</p><p class="text-2xs capitalize text-fg-subtle">{entry.status}{entry.queue_position !== null ? ` · queue #${entry.queue_position}` : ""}</p></div>
									<span class="h-2 w-2 rounded-full {entry.status === "running" ? "bg-accent animate-pulse" : "bg-warning"}"></span>
								</div>
							{/each}
						</div>
					</div>
				{/if}
			</div>

			<span class="h-[26px] w-px flex-shrink-0 self-center bg-line" aria-hidden="true"></span>
			<div class="flex items-center gap-0.5 px-3.5">
				<Tooltip text="Generation settings" position="top" delay={150}>
					<button type="button" class="inline-flex h-[34px] w-[34px] items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg disabled:cursor-not-allowed disabled:text-fg-disabled disabled:hover:bg-transparent" on:click={() => toggleDrawer("settings")} disabled={!$$slots.settings} aria-label="Generation settings" aria-pressed={activeDrawer === "settings"}>
						<Icon name="sliders" className="h-4 w-4" />
					</button>
				</Tooltip>
				<Tooltip text="Last generations" position="top" delay={150}>
					<button type="button" class="inline-flex h-[34px] w-[34px] items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg disabled:cursor-not-allowed disabled:text-fg-disabled disabled:hover:bg-transparent" on:click={() => toggleDrawer("lastGenerations")} disabled={!$$slots.lastGenerations} aria-label="Last generations" aria-pressed={activeDrawer === "lastGenerations"}>
						<Icon name="clock" className="h-4 w-4" />
					</button>
				</Tooltip>
				<Tooltip text={modeChromeTooltip} position="top" delay={150}>
					<button
						type="button"
						class="inline-flex h-[34px] w-[34px] items-center justify-center rounded transition-colors disabled:cursor-not-allowed disabled:text-fg-disabled disabled:hover:bg-transparent
							{modeChromeGlyph === 'stopping' ? 'bg-warning/10 text-warning' : modeChromeActive ? 'bg-signal/10 text-signal' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
						on:click={handleModeChromeClick}
						disabled={modeChromeDisabled}
						aria-label={modeChromeTooltip}
					>
						<Icon name={modeChromeIcon} className="h-4 w-4" />
					</button>
				</Tooltip>
				{#each $panelModeContributions as modeContrib (pluginDrawerId(modeContrib))}
					<Tooltip text={modeContrib.label || modeContrib.component} position="top" delay={150}>
						<button type="button" class="inline-flex h-[34px] w-[34px] items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg" on:click={() => toggleDrawer(pluginDrawerId(modeContrib))} aria-label={modeContrib.label || modeContrib.component} aria-pressed={activeDrawer === pluginDrawerId(modeContrib)}>
							<Icon name="extension" className="h-4 w-4" />
						</button>
					</Tooltip>
				{/each}
			</div>

			<Tooltip text={markLabel} kbd={markState !== "running" ? $shortcutLabels["start_generation"] : undefined} position="top" delay={150}>
				<GenerateMark state={markState} disabled={markState === "disabled"} label={markLabel} onclick={handleMarkClick} />
			</Tooltip>
		</div>
	</div>
</div>

<style>
	:global(.tabular-nums) {
		font-variant-numeric: tabular-nums;
	}

	.overflow-y-auto::-webkit-scrollbar {
		width: 8px;
	}

	.overflow-y-auto::-webkit-scrollbar-track {
		background: transparent;
	}

	.overflow-y-auto::-webkit-scrollbar-thumb {
		background: rgb(var(--line-strong));
		border-radius: 4px;
	}

	.overflow-y-auto::-webkit-scrollbar-thumb:hover {
		background: rgb(var(--line-hover));
	}

	/* Indeterminate progress: no known fraction yet, so slide a fixed-width
	   segment instead of pinning the bar at 0% (reads as hung, not working). */
	.progress-indeterminate {
		animation: progress-indeterminate-slide 1.2s ease-in-out infinite;
	}

	@keyframes progress-indeterminate-slide {
		0% {
			transform: translateX(-100%);
		}
		100% {
			transform: translateX(300%);
		}
	}
</style>
