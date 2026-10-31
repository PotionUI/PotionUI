<script lang="ts">
	import { deckStepText } from '$lib/generation/previewClip';
	import { onMount, onDestroy, tick, createEventDispatcher } from 'svelte';
	import dismiss from '$lib/actions/dismiss';
	import { browser } from '$app/environment';
	import { storage } from '$lib/utils/storage';
	import type { GenerationState } from '$lib/types/tabs';
	import { authStore } from '$lib/stores/auth';
	import { readBatchProgress } from '$lib/generation/batchProgress';
	import { resolveStageStatus } from './generation-panel/stageLabels';
	import { contributionsForSlot } from '$lib/extensions/extensionSlots';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import Tooltip from './Tooltip.svelte';
	import GenerateMark from './generation-panel/GenerateMark.svelte';
	import DeckThumb from './generation-panel/DeckThumb.svelte';
	import AmbientGlow from './AmbientGlow.svelte';
	import { deriveMarkState, deriveModeChromeGlyph, formatDurationMs, formatDurationSeconds } from './generation-panel/barState';
	import { deckEyebrow, deckTitle, deckTone, deriveDeckState, shouldAnnounceQuarter, type DeckState } from './generation-panel/deckStatus';
	import { deckAmbientSource, deriveDeckThumb, hasDeckResult, type DeckThumbPreset } from './generation-panel/deckThumb';
	import { createStatusLineRotator, phaseFor } from './generation-panel/statusLines';
	import { playfulStatusLines } from '$lib/stores/playfulStatusLines';
	import { shortcutLabels } from '$lib/stores/keybindings';
	import { createGenerationModeController } from './generationModeController';
	import {
		armCompare,
		compareRevision,
		compareProgressRevision,
		readCompareState,
		readCompareSummary,
		toggleCompareDrawer
	} from '$lib/generation/compare/compareStore.svelte';
	import { limits, limitsMeta } from '$lib/plans/store';
	import { gridFraction } from '$lib/generation/compare/view/gridModel';
	import { compareStatusLine } from '$lib/generation/compare/compareSummary';
	import CompareDrawer from './compare/CompareDrawer.svelte';
	import GenerationDetailsModal from './modals/GenerationDetailsModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Menu, AdminOnlyMark } from '$lib/components/ui';

	const dispatch = createEventDispatcher();

	$: panelModeContributions = contributionsForSlot('generation.panel.modes');
	const pluginDrawerId = (c: { plugin_id: string; component: string }) => `plugin:${c.plugin_id}:${c.component}`;
	$: activePanelModeContribution = $panelModeContributions.find((c) => pluginDrawerId(c) === activeDrawer);

	export let generation: GenerationState;
	export let isGenerating: boolean = false;
	export let onGenerate: (() => void) | undefined = undefined;
	export let onCancel: (() => void) | undefined = undefined;
	export let canGenerate: boolean = false;
	export let disabledReason: string | undefined = undefined;
	export let costNote: string | null = null;
	export let batchLine: { text: string; generateLabel: string; over: boolean } | null = null;
	export let generatingTabName: string | undefined = undefined;
	export let isActiveTabGenerating: boolean = false;
	export let onSwitchToGeneratingTab: (() => void) | undefined = undefined;
	export let onClearQueue: (() => void) | undefined = undefined;
	export let tabId: string = '';
	export let workbenchFloating: boolean = false;
	export let preset: DeckThumbPreset | null = null;
	export let modeLabel: string = '';

	$: queueEntries = generation.queue || [];
	$: queueDepth = queueEntries.length;

	let mounted = false;
	let mainElement: HTMLElement | null = null;

	type DrawerType = 'settings' | 'lastGenerations' | string | null;
	let activeDrawer: DrawerType = null;
	const STORAGE_KEY_DRAWER = 'generation-panel-active-drawer';

	export function toggleDrawer(drawer: DrawerType) {
		if (workbenchFloating) return;
		activeDrawer = activeDrawer === drawer ? null : drawer;
		if (activeDrawer) {
			storage.set(STORAGE_KEY_DRAWER, activeDrawer);
		} else {
			storage.remove(STORAGE_KEY_DRAWER);
		}
	}

	function closeDrawer() {
		activeDrawer = null;
		storage.remove(STORAGE_KEY_DRAWER);
	}

	$: if (workbenchFloating && activeDrawer) {
		activeDrawer = null;
	}

	const modeController = createGenerationModeController(() => handleGenerate());
	const { mode: generationMode, stopAfterCurrentRequested } = modeController;
	let showQueuePopover = false;
	let queuePopoverRef: HTMLElement;
	let utilitiesRef: HTMLElement;
	let panelRef: HTMLElement;
	let queuePopoverStyle = '';
	let detailsOpen = false;

	async function positionQueuePopover() {
		await tick();
		if (!queuePopoverRef || !utilitiesRef || !panelRef) return;
		const rect = utilitiesRef.getBoundingClientRect();
		const width = queuePopoverRef.offsetWidth || 330;
		const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width));
		const bottom = window.innerHeight - panelRef.getBoundingClientRect().top + 8;
		queuePopoverStyle = `left:${left}px; bottom:${bottom}px;`;
	}

	function toggleQueuePopover() {
		showQueuePopover = !showQueuePopover;
		if (showQueuePopover) positionQueuePopover();
	}

	function handleWindowResize() {
		showQueuePopover = false;
	}

	$: if (workbenchFloating && showQueuePopover) {
		showQueuePopover = false;
	}

	$: markState = deriveMarkState({ isGenerating, canGenerate: mounted && markCanGenerate, mode: $generationMode });
	$: modeChromeGlyph = deriveModeChromeGlyph({
		isGenerating,
		mode: $generationMode,
		stopAfterCurrentRequested: $stopAfterCurrentRequested
	});
	$: modeChromeActive = modeChromeGlyph === 'idle' && $generationMode === 'forever';
	$: compareState = readCompareState($compareRevision, tabId);
	$: compareArmed = compareState.config.armed && !compareState.blocked;
	$: compareSummary = readCompareSummary($compareRevision, tabId, $limits, $limitsMeta?.contactLine ?? undefined);
	$: compareResolved = compareState.cells.filter((cell) => cell.status === 'completed' || cell.status === 'failed' || cell.status === 'cancelled').length;
	$: compareGridActive = compareState.gridRunning;
	$: compareConflict = $generationMode === 'forever';
	$: compareButtonDisabled = !!compareState.blocked || (compareConflict && !compareArmed) || workbenchFloating || !tabId;
	$: compareTooltip = compareState.blocked
		? compareState.blocked
		: compareConflict && !compareArmed
			? "Compare can't run while Continuous is on. Turn Continuous off first."
			: compareArmed
				? compareState.drawerOpen
					? 'Close the compare settings'
					: 'Edit the compared fields'
				: 'Compare two fields side by side';
	$: markCanGenerate = compareArmed ? canGenerate && !compareSummary.disabledReason : canGenerate;
	$: modeChromeDisabled =
		modeChromeGlyph === 'stopping' ||
		(modeChromeGlyph === 'idle' && (compareArmed || !(mounted && canGenerate)));
	$: modeChromeTooltip =
		compareArmed && modeChromeGlyph === 'idle'
			? "Continuous can't run while Compare is on. Turn Compare off first."
			: modeChromeGlyph === 'stopping'
				? 'Finishing this generation, then stopping continuous mode'
				: modeChromeGlyph === 'pause'
					? 'Stop after current generation'
					: $generationMode === 'forever'
						? 'Mode: Continuous'
						: 'Mode: Generate once';
	$: modeChromeSpriteId =
		modeChromeGlyph === 'pause' ? 'panel-pause' : modeChromeGlyph === 'stopping' ? 'panel-hourglass' : 'panel-loop';

	function handleCompareClick() {
		if (compareButtonDisabled) return;
		if (!compareArmed) {
			modeController.setMode('once');
			armCompare(tabId);
			return;
		}
		toggleCompareDrawer(tabId);
	}

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

	$: effectiveDisabledReason = compareArmed ? (compareSummary.disabledReason ?? disabledReason) : disabledReason;
	$: markLabel =
		markState === 'running'
			? compareGridActive
				? 'Cancel all comparison cells'
				: 'Cancel generation'
			: markState === 'disabled' && effectiveDisabledReason
				? effectiveDisabledReason
				: compareArmed
					? `Generate ${compareSummary.count} comparison cells`
					: batchLine
						? batchLine.generateLabel
						: $generationMode === 'forever'
							? 'Start continuous generation'
							: 'Generate';

	let generationStartTime: number | null = null;
	let generationEndTime: number | null = null;
	let timerInterval: ReturnType<typeof setInterval> | null = null;
	let elapsedSeconds = 0;
	let motionQuery: MediaQueryList | null =
		typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
	let reducedMotion = motionQuery?.matches ?? false;

	function handleMotionChange(event: MediaQueryListEvent) {
		reducedMotion = event.matches;
	}

	onMount(() => {
		mounted = true;
		mainElement = document.querySelector('main');
		const savedDrawer = storage.get(STORAGE_KEY_DRAWER);
		if (savedDrawer === 'settings' || savedDrawer === 'lastGenerations') {
			activeDrawer = savedDrawer;
		}
		motionQuery?.addEventListener?.('change', handleMotionChange);
		window.addEventListener('resize', handleWindowResize);
		return () => {
			window.removeEventListener('resize', handleWindowResize);
			motionQuery?.removeEventListener?.('change', handleMotionChange);
		};
	});

	$: if (browser) {
		const overflow = activeDrawer ? 'hidden' : '';
		document.documentElement.style.overflow = overflow;
		document.body.style.overflow = overflow;
		if (mainElement) mainElement.style.overflow = overflow;
	}

	let wasGenerating = false;

	$: {
		if (!wasGenerating && isGenerating) {
			generationStartTime = generation.startedAt ?? Date.now();
			generationEndTime = null;
			elapsedSeconds = Math.max(0, (Date.now() - generationStartTime) / 1000);
			if (timerInterval) clearInterval(timerInterval);
			timerInterval = setInterval(() => {
				if (generationStartTime) {
					elapsedSeconds = (Date.now() - generationStartTime) / 1000;
				}
			}, 500);
			wasGenerating = true;
		}

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
		activeDrawer = null;
		generationStartTime = null;
		generationEndTime = null;
		elapsedSeconds = 0;
		modeController.handleGenerationStart();
		dispatch('generationstart');
		onGenerate?.();
	}

	function handleCancel() {
		modeController.cancel();
		onCancel?.();
	}

	function handleStopAfterCurrent() {
		modeController.requestStopAfterCurrent();
	}

	function gridPercent(_revision: number, cells: typeof compareState.cells): number {
		return Math.round(gridFraction({ cells }) * 100);
	}

	$: hasProgressFraction = compareGridActive || generation.currentProgress?.progress != null;
	$: progressFraction = compareGridActive
		? gridFraction({ cells: compareState.cells })
		: (generation.currentProgress?.progress ?? 0);
	$: progressPercent = compareGridActive
		? gridPercent($compareProgressRevision, compareState.cells)
		: Math.round((generation.currentProgress?.progress ?? 0) * 100);
	$: isAdmin = $authStore.user?.account_type === 'ADMIN';
	$: stage = resolveStageStatus({
		currentStep: generation.currentProgress?.current_step,
		message: generation.currentProgress?.message,
		pipeName: generation.currentProgress?.pipe_name,
		pipeId: generation.currentProgress?.pipe_id,
		isAdmin
	});
	$: batchReading = readBatchProgress(generation.currentProgress);
	$: clipStep = isGenerating ? Number((generation.currentGeneration as any)?.preview_clip?.step ?? 0) : 0;
	$: stepText = deckStepText(stage.stepCount, clipStep);

	$: elapsedText = isGenerating ? formatDurationSeconds(elapsedSeconds) : 'none';
	$: lastText = formatDurationMs(generation.lastDurationMs ?? null);

	$: currentGeneration = generation.currentGeneration;
	$: detailsId = String(currentGeneration?.id ?? currentGeneration?.generation_id ?? '');
	$: otherTab = generatingTabName && !isActiveTabGenerating ? generatingTabName : null;
	$: deckState = deriveDeckState({
		isGenerating,
		hasProgressFraction,
		otherTabName: otherTab,
		disabled: markState === 'disabled' && !!effectiveDisabledReason,
		resultStatus: currentGeneration?.status ?? null
	});
	$: thumbView = deriveDeckThumb({ generation, isGenerating, preset });
	$: ambientSource = deckAmbientSource(thumbView);
	$: hasResult = hasDeckResult(isGenerating ? deriveDeckThumb({ generation, isGenerating: false, preset }) : thumbView);
	$: title = deckTitle({
		state: deckState,
		otherTabName: otherTab,
		compareArmed,
		stopping: $stopAfterCurrentRequested,
		hasResult,
		playful: $playfulStatusLines
	});
	$: eyebrow = deckEyebrow(deckState, hasResult);
	$: dotTone = deckTone(deckState);
	$: failureText = String(currentGeneration?.message ?? currentGeneration?.hint ?? '');
	$: restingDetail = preset?.name ? [preset.name, modeLabel].filter(Boolean).join(' · ') : 'Configure the prompt and settings';
	$: runningDetail = [
		hasProgressFraction ? `${progressPercent}%` : null,
		compareGridActive ? `${compareResolved}/${compareState.cells.length} cells` : null,
		stage.label,
		stepText,
		...stage.meta.map((marker) => marker.value),
		$stopAfterCurrentRequested ? 'stopping after this one' : $generationMode === 'forever' ? 'continuous' : null
	]
		.filter(Boolean)
		.join(' · ');

	let phrase = '';
	let phraseFading = false;
	const rotator = createStatusLineRotator({
		onLine: (line) => (phrase = line),
		onFade: (fading) => (phraseFading = fading),
		reducedMotion: () => reducedMotion
	});
	$: runKey = String(currentGeneration?.id ?? currentGeneration?.generation_id ?? generation.startedAt ?? '');
	$: phraseActive = isGenerating && $playfulStatusLines && !$stopAfterCurrentRequested;
	$: rotator.sync(phraseActive ? phaseFor(hasProgressFraction ? progressFraction : null, reducedMotion) : null, runKey);
	$: visibleTitle = phraseActive && phrase ? phrase : title.visible;

	let announcement = '';
	let announcedQuarter = 0;
	let previousState: DeckState | null = null;

	function announce(state: DeckState, percent: number) {
		const prior = previousState;
		previousState = state;
		const active = state === 'running' || state === 'loading';
		const wasActive = prior === 'running' || prior === 'loading';
		if (prior === null) return;
		if (active && !wasActive) {
			announcedQuarter = 0;
			announcement = 'Generation started';
			return;
		}
		if (state === 'running') {
			const quarter = shouldAnnounceQuarter(percent, announcedQuarter);
			if (quarter !== null) {
				announcedQuarter = quarter;
				announcement = `${quarter}% generated`;
			}
			return;
		}
		if (!wasActive || state === prior) return;
		if (state === 'done') announcement = 'Generation complete';
		else if (state === 'failed') announcement = failureText ? `Generation failed: ${failureText}` : 'Generation failed';
		else announcement = 'Generation cancelled';
	}

	$: announce(deckState, progressPercent);

	onDestroy(() => {
		if (timerInterval) {
			clearInterval(timerInterval);
			timerInterval = null;
		}
		rotator.dispose();
		modeController.dispose();
		if (browser) {
			document.documentElement.style.overflow = '';
			document.body.style.overflow = '';
			if (mainElement) mainElement.style.overflow = '';
		}
	});

	$: drawerWidth =
		activeDrawer === 'lastGenerations'
			? 'min(480px, calc(100vw - 36px))'
			: activeDrawer && activeDrawer !== 'settings'
				? 'min(1000px, calc(100vw - 36px))'
				: undefined;

	$: queueText = queueDepth > 0 ? `${queueDepth} ${queueDepth === 1 ? 'job' : 'jobs'}` : 'Empty';
	$: drawerUnavailable = workbenchFloating ? 'Unavailable while the workbench is floating' : '';

	const MENU_ROW =
		'flex h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-sm text-fg hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50';
</script>

<div class="generation-panel" data-menu-dock data-state={deckState} aria-label="Generation controls" bind:this={panelRef}>
	<div class="deck-ambient">
		<AmbientGlow src={ambientSource} class="deck-halo" crossfade={400} {reducedMotion} />
	</div>

	{#if activeDrawer}
		<div
			class="backdrop"
			on:click={closeDrawer}
			on:keydown={(e) => e.key === 'Escape' && closeDrawer()}
			role="button"
			tabindex="-1"
			aria-label="Close drawer"
		></div>

		<aside class="drawer" style={drawerWidth ? `width: ${drawerWidth}` : ''} aria-label="Panel drawer">
			<header class="drawer-header">
				<div class="drawer-header-copy">
					<h2>
						{#if activeDrawer === 'settings'}
							Generation Settings
						{:else if activeDrawer === 'lastGenerations'}
							Last Generations
						{:else if activePanelModeContribution}
							{activePanelModeContribution.label || activePanelModeContribution.component}
						{/if}
					</h2>
				</div>
				<button type="button" class="close-button" on:click={closeDrawer} aria-label="Close drawer">
					<Icon name="panel-x" className="icon" strokeWidth={1.8} />
				</button>
			</header>

			<div class="drawer-body {activeDrawer === 'settings' ? '' : 'hidden'}">
				<slot name="settings" />
			</div>

			{#if activeDrawer === 'lastGenerations'}
				<div class="drawer-body">
					<slot name="lastGenerations" />
				</div>
			{/if}

			{#each $panelModeContributions as modeContrib (pluginDrawerId(modeContrib))}
				{#if activeDrawer === pluginDrawerId(modeContrib)}
					<div class="drawer-body">
						{#await resolvePluginComponent(modeContrib.plugin_id, modeContrib.component) then Component}
							{#if Component}
								<svelte:component this={Component} {generation} />
							{/if}
						{/await}
					</div>
				{/if}
			{/each}
		</aside>
	{/if}

	<div
		class="progress-track"
		role="progressbar"
		aria-label="Generation progress"
		aria-valuemin="0"
		aria-valuemax="100"
		aria-valuenow={isGenerating && hasProgressFraction ? progressPercent : undefined}
		hidden={!isGenerating}
	>
		<div class="progress-fill" style={isGenerating && hasProgressFraction ? `width: ${progressPercent}%` : ''}></div>
	</div>

	<div class="deck">
		<section class="deck-status" aria-label="Generation status">
			<DeckThumb view={thumbView} />
			<div class="deck-copy">
				<span class="deck-eyebrow">{eyebrow}</span>
				<div class="deck-title-line">
					<span class="deck-dot is-{dotTone}" aria-hidden="true"></span>
					<span class="deck-title {phraseFading ? 'is-fading' : ''}" aria-hidden="true" data-deck-title>{visibleTitle}</span>
					<span class="sr-only" data-deck-literal>{title.literal}</span>
				</div>
				<div class="deck-detail">
					<span class="deck-detail-text" class:is-wrapping={clipStep > 0}>
						{#if isGenerating}
							{#if batchReading}
								<span class="font-mono tabular-nums" data-batch-progress>{batchReading.label}</span> ·
							{/if}
							{runningDetail}
							{#if stage.technical}
								<span class="ml-1.5 inline-flex align-middle" data-admin-stage-detail><AdminOnlyMark text={stage.technical} /></span>
							{/if}
						{:else if otherTab}
							Another tab currently owns the generation worker
						{:else if generation.cancelNotice}
							{generation.cancelNotice}
						{:else if deckState === 'failed' && failureText}
							{failureText}
						{:else if compareArmed}
							<span class="font-mono tabular-nums uppercase" data-compare-status>
								{markState === 'disabled' && effectiveDisabledReason
									? effectiveDisabledReason
									: compareStatusLine(compareSummary.count, compareSummary.estimate)}
							</span>
						{:else if batchLine}
							{#if markState === 'disabled' && disabledReason && !batchLine.over}
								<span>{disabledReason} ·</span>
							{/if}
							<span class="font-mono tabular-nums" data-batch-line>{batchLine.text}</span>
						{:else if costNote && !(markState === 'disabled' && disabledReason)}
							<span class="font-mono tabular-nums" data-cost-note>{costNote}</span>
						{:else}
							{markState === 'disabled' && disabledReason
								? disabledReason
								: $generationMode === 'forever'
									? 'Continuous mode armed'
									: restingDetail}
						{/if}
					</span>
					{#if deckState === 'other'}
						<button type="button" class="inline-action" on:click={() => onSwitchToGeneratingTab?.()}>
							Switch to tab <Icon name="panel-arrow" className="icon" strokeWidth={1.8} />
						</button>
					{:else if deckState === 'failed' && detailsId}
						<button type="button" class="inline-action" on:click={() => (detailsOpen = true)}>
							Details <Icon name="panel-arrow" className="icon" strokeWidth={1.8} />
						</button>
					{/if}
				</div>
			</div>
		</section>

		<section class="run-cluster" aria-label="Run controls">
			<Tooltip text={compareTooltip} position="top" delay={150}>
				<button
					type="button"
					class="mode-button {compareArmed ? 'is-continuous' : ''}"
					aria-label={compareTooltip}
					aria-pressed={compareArmed}
					aria-expanded={compareArmed ? compareState.drawerOpen : undefined}
					data-compare-toggle
					disabled={compareButtonDisabled}
					on:click={handleCompareClick}
				>
					<svg class="icon" viewBox="0 0 24 24"><path d="M4 4h6.5v6.5H4ZM13.5 4H20v6.5h-6.5ZM4 13.5h6.5V20H4ZM13.5 13.5H20V20h-6.5Z" /></svg>
					{#if compareArmed && compareSummary.count > 0}<span class="compare-count">{compareSummary.count}</span>{/if}
				</button>
			</Tooltip>
			<Tooltip text={markLabel} kbd={markState !== 'running' ? $shortcutLabels['start_generation'] : undefined} position="top" delay={150}>
				<GenerateMark state={markState} disabled={markState === 'disabled'} label={markLabel} onclick={handleMarkClick} />
			</Tooltip>
			<Tooltip text={modeChromeTooltip} position="top" delay={150}>
				<button
					type="button"
					class="mode-button {modeChromeActive ? 'is-continuous' : ''} {modeChromeGlyph === 'stopping' ? 'is-stopping' : ''}"
					aria-label={modeChromeTooltip}
					aria-pressed={$generationMode === 'forever'}
					disabled={modeChromeDisabled}
					on:click={handleModeChromeClick}
				>
					<Icon name={modeChromeSpriteId} className="icon" strokeWidth={1.8} />
				</button>
			</Tooltip>
		</section>

		<section class="utility-cluster" aria-label="Run information and tools" bind:this={utilitiesRef}>
			{#if isGenerating}
				<div class="deck-timing">
					<span class="cell-label">Elapsed</span>
					<span class="cell-value">{elapsedText}</span>
				</div>
			{:else}
				<Tooltip text="Run details" position="top" delay={150}>
					<button
						type="button"
						class="deck-timing"
						aria-label="Run details, last {lastText === 'none' ? 'none' : lastText}"
						disabled={!detailsId}
						on:click={() => (detailsOpen = true)}
					>
						<span class="cell-label">Last</span>
						<span class="cell-value">{lastText === 'none' ? '—' : lastText}</span>
					</button>
				</Tooltip>
			{/if}
			<Tooltip text={drawerUnavailable || 'Queue'} position="top" delay={150} wrapperClass="hidden lg:inline-flex lg:items-center">
				<button type="button" class="icon-button" aria-label="Generation queue" aria-expanded={showQueuePopover} disabled={workbenchFloating} on:click={toggleQueuePopover}>
					<Icon name="list-plus" className="icon" strokeWidth={1.8} />
					{#if queueDepth > 0}<span class="queue-count">{queueDepth}</span>{/if}
				</button>
			</Tooltip>
			<Tooltip text={drawerUnavailable || 'Generation settings'} position="top" delay={150} wrapperClass="hidden lg:inline-flex lg:items-center">
				<button
					type="button"
					class="icon-button"
					aria-label="Generation settings"
					aria-expanded={activeDrawer === 'settings'}
					disabled={!$$slots.settings || workbenchFloating}
					on:click={() => toggleDrawer('settings')}
				>
					<Icon name="panel-sliders" className="icon" strokeWidth={1.8} />
				</button>
			</Tooltip>
			<Tooltip text={drawerUnavailable || 'Last generations'} position="top" delay={150} wrapperClass="hidden lg:inline-flex lg:items-center">
				<button
					type="button"
					class="icon-button"
					aria-label="Last generations"
					aria-expanded={activeDrawer === 'lastGenerations'}
					disabled={!$$slots.lastGenerations || workbenchFloating}
					on:click={() => toggleDrawer('lastGenerations')}
				>
					<Icon name="panel-history" className="icon" strokeWidth={1.8} />
				</button>
			</Tooltip>
			{#each $panelModeContributions as modeContrib (pluginDrawerId(modeContrib))}
				<Tooltip text={drawerUnavailable || modeContrib.label || modeContrib.component} position="top" delay={150} wrapperClass="hidden lg:inline-flex lg:items-center">
					<button
						type="button"
						class="icon-button"
						aria-label={modeContrib.label || modeContrib.component}
						aria-expanded={activeDrawer === pluginDrawerId(modeContrib)}
						disabled={workbenchFloating}
						on:click={() => toggleDrawer(pluginDrawerId(modeContrib))}
					>
						<Icon name="panel-extension" className="icon" strokeWidth={1.8} />
					</button>
				</Tooltip>
			{/each}
			<Menu label="More generation actions" width={248} align="right" preferred="up">
				{#snippet trigger(menu)}
					<button
						type="button"
						class="icon-button"
						aria-label="More generation actions"
						aria-haspopup="menu"
						aria-expanded={menu.open}
						data-deck-more
						on:click={menu.toggle}
					>
						<Icon name="more" className="icon" strokeWidth={1.8} />
					</button>
				{/snippet}
				{#snippet children(menu)}
					{#if isGenerating}
						<div class="flex h-9 items-center gap-2.5 px-2.5 text-sm text-fg-muted md:hidden" data-deck-more-elapsed>
							<Icon name="panel-hourglass" className="h-4 w-4 flex-shrink-0" />
							<span class="min-w-0 flex-1 truncate">Elapsed</span>
							<span class="font-mono text-sm tabular-nums text-fg">{elapsedText}</span>
						</div>
					{/if}
					<button
						type="button"
						role="menuitem"
						class="{MENU_ROW} lg:hidden"
						disabled={workbenchFloating}
						on:click={() => {
							menu.close();
							toggleQueuePopover();
						}}
					>
						<Icon name="list-plus" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
						<span class="min-w-0 flex-1 truncate">Queue</span>
						<span class="font-mono text-sm tabular-nums text-fg-muted">{queueText}</span>
					</button>
					<button
						type="button"
						role="menuitem"
						class="{MENU_ROW} lg:hidden"
						disabled={!$$slots.settings || workbenchFloating}
						on:click={() => {
							menu.close();
							toggleDrawer('settings');
						}}
					>
						<Icon name="panel-sliders" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
						<span class="min-w-0 flex-1 truncate">Generation settings</span>
					</button>
					<button
						type="button"
						role="menuitem"
						class="{MENU_ROW} lg:hidden"
						disabled={!$$slots.lastGenerations || workbenchFloating}
						on:click={() => {
							menu.close();
							toggleDrawer('lastGenerations');
						}}
					>
						<Icon name="panel-history" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
						<span class="min-w-0 flex-1 truncate">Last generations</span>
					</button>
					{#each $panelModeContributions as modeContrib (pluginDrawerId(modeContrib))}
						<button
							type="button"
							role="menuitem"
							class="{MENU_ROW} lg:hidden"
							disabled={workbenchFloating}
							on:click={() => {
								menu.close();
								toggleDrawer(pluginDrawerId(modeContrib));
							}}
						>
							<Icon name="panel-extension" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
							<span class="min-w-0 flex-1 truncate">{modeContrib.label || modeContrib.component}</span>
						</button>
					{/each}
					<button
						type="button"
						role="menuitem"
						class={MENU_ROW}
						disabled={isGenerating || !detailsId}
						on:click={() => {
							menu.close();
							detailsOpen = true;
						}}
					>
						<Icon name="info" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
						<span class="min-w-0 flex-1 truncate">Run details</span>
						<span class="font-mono text-sm tabular-nums text-fg-muted">{lastText === 'none' ? '—' : lastText}</span>
					</button>
					<div class="my-1 h-px bg-line" role="separator"></div>
					<button
						type="button"
						role="menuitem"
						class="{MENU_ROW} !text-danger"
						disabled={queueDepth === 0}
						on:click={() => {
							menu.close();
							onClearQueue?.();
						}}
					>
						<Icon name="panel-trash" className="h-4 w-4 flex-shrink-0" />
						<span class="min-w-0 flex-1 truncate">Cancel queued jobs</span>
					</button>
				{/snippet}
			</Menu>
		</section>
	</div>

	<span class="sr-only" role="status" aria-live="polite" data-deck-announcer>{announcement}</span>

	{#if tabId}
		<CompareDrawer
			{tabId}
			canGenerate={markCanGenerate}
			generateDisabledReason={effectiveDisabledReason}
			{isGenerating}
			onGenerate={handleMarkClick}
		/>
	{/if}

	{#if showQueuePopover}
		<div
			class="floating-panel"
			style={queuePopoverStyle}
			bind:this={queuePopoverRef}
			use:dismiss={{ onDismiss: () => (showQueuePopover = false), ignore: [utilitiesRef], escape: false }}
			role="dialog"
			aria-label="Generation queue"
		>
			<div class="popover-header">
				<strong>Generation queue</strong>
				<button
					type="button"
					class="quiet-button danger"
					on:click={() => {
						showQueuePopover = false;
						onClearQueue?.();
					}}
				>
					<Icon name="panel-trash" className="icon" strokeWidth={1.8} />Cancel all
				</button>
			</div>
			<div class="queue-list">
				{#if queueEntries.length === 0}
					<div class="queue-row">
						<span class="row-copy">
							<span class="row-title">Queue is empty</span>
							<span class="row-meta">New requests will appear here.</span>
						</span>
					</div>
				{:else}
					{#each queueEntries as entry, index (entry.generation_id)}
						<div class="queue-row">
							<span class="job-index">{String(index + 1).padStart(2, '0')}</span>
							<span class="row-copy">
								<span class="row-title">{entry.generation_id}</span>
								<span class="row-meta">{entry.queue_position !== null ? `Queue #${entry.queue_position}` : entry.status}</span>
							</span>
							<span class="row-status {entry.status === 'running' ? 'live' : ''}">{entry.status}</span>
						</div>
					{/each}
				{/if}
			</div>
			<div class="popover-footer"><span>Jobs run in the order they were added.</span></div>
		</div>
	{/if}

	{#if detailsOpen && detailsId}
		<GenerationDetailsModal
			isOpen={detailsOpen}
			generationId={detailsId}
			initialFileIndex={generation.workbenchIndex ?? 0}
			on:close={() => (detailsOpen = false)}
		/>
	{/if}
</div>

<style>
	:global(:root) {
		--deck-height: 92px;
		--dock-height: var(--deck-height);
	}

	@media (max-width: 767px) {
		:global(:root) {
			--deck-height: 140px;
			--dock-height: calc(var(--deck-height) + var(--tab-bar-height));
		}
	}

	:global(:root:has(.generation-panel)) {
		--tab-bar-lift: var(--deck-height);
		--install-banner-z: 20;
	}
</style>
