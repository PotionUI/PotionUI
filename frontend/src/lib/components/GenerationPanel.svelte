<script lang="ts">
	import { onMount, onDestroy, tick, createEventDispatcher } from 'svelte';
	import { browser } from '$app/environment';
	import { storage } from '$lib/utils/storage';
	import type { GenerationState } from '$lib/types/tabs';
	import type { PresetModeVariant } from '$lib/types/api';
	import { parseTemplateMarkers } from '$lib/utils/templateProcessor';
	import { contributionsForSlot } from '$lib/extensions/extensionSlots';
	import { resolvePluginComponent } from '$lib/plugin-api/componentResolver';
	import Tooltip from './Tooltip.svelte';
	import GenerationPanelIconSprite from './generation-panel/GenerationPanelIconSprite.svelte';
	import GenerateMark from './generation-panel/GenerateMark.svelte';
	import PanelReadoutCell from './generation-panel/PanelReadoutCell.svelte';
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
	// While the floating workbench (`W`) is open, its window sits above this
	// panel's own drawers (both z-30, whichever mounted later wins) — block
	// opening a second overlay on top of it instead of letting them stack.
	export let workbenchFloating: boolean = false;

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

	// Toggle drawer open/closed - exported so the page's keybinding handler can drive it
	export function toggleDrawer(drawer: DrawerType) {
		if (workbenchFloating) return;
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

	// Force-close without touching storage: the user's last-chosen drawer
	// still comes back on the next mount, the workbench going floating only
	// hides it for now.
	$: if (workbenchFloating && activeDrawer) {
		activeDrawer = null;
	}

	// Generation mode state
	const modeController = createGenerationModeController(() => handleGenerate());
	const { mode: generationMode, stopAfterCurrentRequested } = modeController;
	let showQueuePopover = false;
	let queuePopoverRef: HTMLElement;
	let queueCellRef: HTMLElement;
	// `.floating-panel` is `position: fixed` (generation-panel-concept.html's
	// own `positionPopover`) rather than an absolutely positioned dropdown,
	// because its real ancestor here is `.context-rail`, which clips
	// overflow — an absolute popover would be invisible under it. Positioned
	// imperatively against the trigger's rect on open; a resize closes it,
	// exactly like the mock.
	let queuePopoverStyle = '';

	async function positionQueuePopover() {
		await tick();
		if (!queuePopoverRef || !queueCellRef) return;
		const rect = queueCellRef.getBoundingClientRect();
		const width = queuePopoverRef.offsetWidth || 288;
		const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.left));
		const bottom = window.innerHeight - rect.top + 8;
		queuePopoverStyle = `left:${left}px; bottom:${bottom}px;`;
	}

	function toggleQueuePopover() {
		showQueuePopover = !showQueuePopover;
		if (showQueuePopover) positionQueuePopover();
	}

	function handleWindowResize() {
		showQueuePopover = false;
	}

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
	$: modeChromeSpriteId =
		modeChromeGlyph === 'pause' ? 'i-pause' : modeChromeGlyph === 'stopping' ? 'i-hourglass' : 'i-loop';

	// data-state drives the ported CSS's status-symbol/progress-track colour
	// and the "other tab" inline-action visibility (generation-panel-concept.html
	// lines 108-149) — the mock's five preview states, mapped onto real
	// generation state instead of a demo tab switcher. "loading" is a
	// generation in flight whose active stage hasn't reported a progress
	// fraction yet (a cold model load); "other" is another tab owning the
	// worker, unchanged from this component's pre-existing branch order.
	$: dataState = isGenerating
		? hasProgressFraction
			? 'running'
			: 'loading'
		: generatingTabName && !isActiveTabGenerating
			? 'other'
			: markState === 'disabled'
				? 'disabled'
				: 'ready';

	// The mock's `.drawer` only ever shows one generic panel at a fixed
	// 380px (generation-panel-concept.html line 317) — real content here
	// needs more: the last-generations gallery and a plugin's own drawer
	// content (e.g. the Video Director shot console) were 480px/1000px
	// before this port and still need to be, or their own layout math
	// (a justified gallery measuring its container, a wide console laid out
	// in columns) puts real elements outside the drawer's `overflow: hidden`
	// box — invisible to hit-testing, so a click there falls through to
	// whatever is behind (the backdrop). Bug found by fe149-drawer-modal-escape.spec.ts.
	$: drawerWidth =
		activeDrawer === 'lastGenerations'
			? 'min(480px, calc(100vw - 36px))'
			: activeDrawer && activeDrawer !== 'settings'
				? 'min(1000px, calc(100vw - 36px))'
				: undefined;

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
		window.addEventListener('resize', handleWindowResize);
		return () => {
			document.removeEventListener('mousedown', handleClickOutside);
			window.removeEventListener('resize', handleWindowResize);
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

<div class="generation-panel" data-state={dataState} aria-label="Generation controls">
	<GenerationPanelIconSprite />

	<!-- Backdrop for drawer -->
	{#if activeDrawer}
		<div
			class="backdrop"
			on:click={closeDrawer}
			on:keydown={(e) => e.key === 'Escape' && closeDrawer()}
			role="button"
			tabindex="-1"
			aria-label="Close drawer"
		></div>

		<!-- Right slide-out drawer -->
		<aside class="drawer" style={drawerWidth ? `width: ${drawerWidth}` : ''} aria-label="Panel drawer">
			<header class="drawer-header">
				<div class="drawer-header-copy">
					<!-- h2, not the mock's bare <strong>: real e2e/a11y contract
						(fe149-drawer-modal-escape.spec.ts) looks this up as a
						heading; `.drawer-header h2` addition below carries the
						same type styling the mock gives `strong`. -->
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
					<svg class="icon"><use href="#i-x" /></svg>
				</button>
			</header>

			<!-- Settings content (app-level generation options, provided by the page) -->
			<div class="drawer-body {activeDrawer === 'settings' ? '' : 'hidden'}">
				<slot name="settings" />
			</div>

			<!-- Last-generations content (recent results for this tab's preset,
				provided by the page). Mounted only while open, like the plugin
				drawers below, so it fetches fresh every time it's opened. -->
			{#if activeDrawer === 'lastGenerations'}
				<div class="drawer-body">
					<slot name="lastGenerations" />
				</div>
			{/if}

			<!-- Plugin `generation.panel.modes` content -->
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

	<!-- `.panel-bar` is purely a width container (maintainer ruling — no
		border/background/shadow of its own); the progress track traces the
		docked strip's own top edge. -->
	<div
		class="progress-track"
		role="progressbar"
		aria-label="Generation progress"
		aria-valuemin="0"
		aria-valuemax="100"
		aria-valuenow={isGenerating && hasProgressFraction ? progressPercent : undefined}
	>
		<div
			class="progress-fill"
			style={isGenerating && hasProgressFraction ? `width: ${progressPercent}%` : ''}
		></div>
	</div>

	<div class="panel-bar">
		<!-- Status block -->
		<section class="status-block" aria-live="polite">
			<div class="status-symbol" aria-hidden="true"></div>
			<div class="status-copy">
				<div class="status-line">
					{#if isGenerating}
						<span class="status-title">Running</span>
					{:else if generatingTabName && !isActiveTabGenerating}
						<span class="status-title">Generating in {generatingTabName}</span>
					{:else}
						<span class="status-title">{markState === 'disabled' && disabledReason ? "Can't generate yet" : 'Ready to generate'}</span>
					{/if}
					{#if isGenerating}
						<span class="status-percent">{hasProgressFraction ? `${progressPercent}%` : 'working…'}</span>
					{/if}
					<button type="button" class="inline-action" on:click={() => onSwitchToGeneratingTab?.()}>
						Switch to tab <svg class="icon" style="width:11px;height:11px"><use href="#i-arrow" /></svg>
					</button>
				</div>
				<span class="status-meta">
					{#if isGenerating}
						{[
							progressMessage,
							currentPipeName,
							...progressMeta.map((marker) => marker.value),
							$stopAfterCurrentRequested ? 'stopping after this one' : $generationMode === 'forever' ? 'continuous' : null
						]
							.filter(Boolean)
							.join(' · ')}
					{:else if generatingTabName && !isActiveTabGenerating}
						Another tab currently owns the generation worker
					{:else}
						{markState === 'disabled' && disabledReason
							? disabledReason
							: $generationMode === 'forever'
								? 'Continuous mode armed'
								: 'Configure the prompt and settings'}
					{/if}
				</span>
			</div>
		</section>

		<!-- Context rail: session · save, last, elapsed, queue. Fixed order,
			every cell always rendered — a value that doesn't exist yet reads
			"none"/"empty" rather than the cell vanishing. -->
		<section class="context-rail" aria-label="Session and run information">
			<SessionCluster {presetId} {currentMode} {tabId} {presetVersion} {availableModes} />

			<PanelReadoutCell label="last" ariaLabel="Last generation duration">
				<span class={lastText === 'none' ? 'text-fg-subtle' : ''}>{lastText}</span>
			</PanelReadoutCell>

			<PanelReadoutCell label="elapsed" ariaLabel="Elapsed time">
				<span class={isGenerating ? '' : 'text-fg-subtle'}>{elapsedText}</span>
			</PanelReadoutCell>

			<PanelReadoutCell
				label="queue"
				clickable={queueDepth > 0}
				ariaLabel="Generation queue"
				onclick={toggleQueuePopover}
				onElement={(el) => (queueCellRef = el)}
			>
				<span class={queueDepth > 0 ? '' : 'text-fg-subtle'}>{queueText}</span>
			</PanelReadoutCell>
		</section>

		<section class="commands" aria-label="Generation actions">
			<div class="utility-cluster">
				<!-- Real Tooltip component here, not the mock's CSS `::after`
					(`data-tooltip` + `content: attr(...)`, neutralized in the
					ported stylesheet): that pseudo-element is positioned
					relative to the button alone, and inside this docked
					`position: fixed` strip it rendered in the wrong place.
					Tooltip computes its own fixed position from the trigger's
					live bounding rect, so it's correct regardless of the
					strip's own stacking context. -->
				<Tooltip
					text={workbenchFloating ? 'Unavailable while the workbench is floating' : 'Generation settings'}
					position="top"
					delay={150}
				>
					<button
						type="button"
						class="icon-button"
						aria-label="Generation settings"
						aria-expanded={activeDrawer === 'settings'}
						disabled={!$$slots.settings || workbenchFloating}
						on:click={() => toggleDrawer('settings')}
					>
						<svg class="icon"><use href="#i-sliders" /></svg>
					</button>
				</Tooltip>
				<Tooltip
					text={workbenchFloating ? 'Unavailable while the workbench is floating' : 'Last generations'}
					position="top"
					delay={150}
				>
					<button
						type="button"
						class="icon-button"
						aria-label="Last generations"
						aria-expanded={activeDrawer === 'lastGenerations'}
						disabled={!$$slots.lastGenerations || workbenchFloating}
						on:click={() => toggleDrawer('lastGenerations')}
					>
						<svg class="icon"><use href="#i-history" /></svg>
					</button>
				</Tooltip>
				{#each $panelModeContributions as modeContrib (pluginDrawerId(modeContrib))}
					<Tooltip
						text={workbenchFloating ? 'Unavailable while the workbench is floating' : (modeContrib.label || modeContrib.component)}
						position="top"
						delay={150}
					>
						<button
							type="button"
							class="icon-button"
							aria-label={modeContrib.label || modeContrib.component}
							aria-expanded={activeDrawer === pluginDrawerId(modeContrib)}
							disabled={workbenchFloating}
							on:click={() => toggleDrawer(pluginDrawerId(modeContrib))}
						>
							<svg class="icon"><use href="#i-extension" /></svg>
						</button>
					</Tooltip>
				{/each}
			</div>
			<div class="run-cluster">
				<Tooltip text={modeChromeTooltip} position="top" delay={150}>
					<button
						type="button"
						class="mode-button {modeChromeActive ? 'is-continuous' : ''} {modeChromeGlyph === 'stopping' ? 'is-stopping' : ''}"
						aria-label={modeChromeTooltip}
						aria-pressed={$generationMode === 'forever'}
						disabled={modeChromeDisabled}
						on:click={handleModeChromeClick}
					>
						<svg class="icon"><use href="#{modeChromeSpriteId}" /></svg>
					</button>
				</Tooltip>
				<Tooltip text={markLabel} kbd={markState !== 'running' ? $shortcutLabels['start_generation'] : undefined} position="top" delay={150}>
					<GenerateMark
						state={markState}
						disabled={markState === 'disabled'}
						label={markLabel}
						shortcut={markState !== 'running' ? $shortcutLabels['start_generation'] : undefined}
						onclick={handleMarkClick}
					/>
				</Tooltip>
			</div>
		</section>
	</div>

	{#if showQueuePopover}
		<!-- role="dialog" per the mock's #queuePopover (a <div>, not a
			<section> — sectioning content can't take an interactive role);
			z-index/positioning come from `.floating-panel` (position: fixed —
			the real ancestor here is `.context-rail`, which clips overflow). -->
		<div
			class="floating-panel"
			style={queuePopoverStyle}
			bind:this={queuePopoverRef}
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
					<svg class="icon"><use href="#i-trash" /></svg>Cancel all
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
</div>

<style>
	/* `--dock-height`: the strip's own height — the mock's `.panel-bar`
	   natural height (min-height: 82px, generation-panel-concept.html;
	   `generation-three-pane-integration.html` reserves the same
	   `--dock-height: 82px`). `.panel-bar` fills the strip exactly
	   (maintainer ruling — it's a width container only, no border/shadow/
	   inset of its own; see the additions override in generation-panel.css).
	   Declared at :root (not scoped to `.generation-panel`) so sibling
	   surfaces that used to hardcode the old 73px bar height —
	   FloatingWorkbench's modal overlay, the three-pane workspace's own
	   bottom padding, the drawer's own `bottom` offset — read the one value
	   instead of drifting from it independently. */
	:global(:root) {
		--dock-height: 82px;
	}

	@media (max-width: 720px) {
		:global(:root) {
			/* The mock's own narrow-layout drawer offset (168px at its 112px
			   default) scaled onto this base, same ratio. */
			--dock-height: 123px;
		}
	}
</style>
