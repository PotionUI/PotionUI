<script lang="ts">
	import { fade } from 'svelte/transition';
	import { tick, onDestroy } from 'svelte';
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';
	import Badge from '../ui/Badge.svelte';
	import Button from '../ui/Button.svelte';
	import IconButton from '../ui/IconButton.svelte';
	import portal from '../../actions/portal';
	import {
		filterResolutionOptions,
		groupOptionsByTier,
		optionTier,
		parseCustomResolution,
		type ResolutionOptionLike
	} from './resolutionPicker';

	interface ResolutionOptionObject extends ResolutionOptionLike {
		value: string;
		description?: string;
		ratio?: number[];
		group?: string;
		tier?: string;
	}
	type ResolutionOption = string | ResolutionOptionObject;

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	let options: ResolutionOption[] = [];
	$: options = config.options || [];
	$: tooltip = config.tooltip;
	$: disabled = config.disabled || false;

	let localValue: string;
	$: localValue = value !== undefined && value !== null && value !== '' ? value : '';

	// Boxes-mode picker panel state.
	let panelOpen = false;
	let searchQuery = '';
	let customMode = false;
	let customWidth = '';
	let customHeight = '';
	let customError: string | null = null;
	let activeRowIndex = 0;
	let triggerRef: HTMLButtonElement | undefined;
	let panelRef: HTMLDivElement | undefined;
	let searchInputRef: HTMLInputElement | undefined;
	let customWidthRef: HTMLInputElement | undefined;
	let panelPosition = { top: 0, bottom: 0, left: 0, width: 0, openUpward: false };

	function parseResolution(res: string) {
		if (!res || typeof res !== 'string') return null;
		const parts = res.split('x');
		if (parts.length !== 2) return null;
		return { width: parseInt(parts[0]), height: parseInt(parts[1]) };
	}

	function gcd(a: number, b: number): number {
		return b === 0 ? a : gcd(b, a % b);
	}

	function getResolutionInfo(res: string) {
		const parsed = parseResolution(res);
		if (!parsed) return null;

		const { width, height } = parsed;
		const divisor = gcd(width, height);
		const ratioW = width / divisor;
		const ratioH = height / divisor;

		return {
			width,
			height,
			ratio: `${ratioW}:${ratioH}`,
			isPortrait: height > width,
			isSquare: width === height,
			isLandscape: width > height
		};
	}

	function handleSelect(res: string) {
		localValue = res;
		if (name) {
			onChange(name, res);
		}
		closePanel();
	}

	function getOptValue(opt: ResolutionOption): string {
		return typeof opt === 'string' ? opt : opt.value;
	}

	function getOptDescription(opt: ResolutionOption): string | null {
		return typeof opt === 'object' && opt.description ? opt.description : null;
	}

	function getOptRatio(opt: ResolutionOption): string | null {
		if (typeof opt === 'object' && opt.ratio && Array.isArray(opt.ratio)) {
			return `${opt.ratio[0]}:${opt.ratio[1]}`;
		}
		return null;
	}

	function getRatioNums(opt: ResolutionOption, info: ReturnType<typeof getResolutionInfo>): [number, number] {
		if (typeof opt === 'object' && Array.isArray(opt.ratio) && opt.ratio.length === 2) {
			return [opt.ratio[0], opt.ratio[1]];
		}
		if (info) {
			const parts = info.ratio.split(':').map(Number);
			if (parts.length === 2 && parts[1] !== 0) return [parts[0], parts[1]];
		}
		return [1, 1];
	}

	function glyphBox(ratioW: number, ratioH: number, maxDim = 16) {
		const viewBox = 24;
		let w: number, h: number;
		if (ratioW >= ratioH) {
			w = maxDim;
			h = ratioW > 0 ? maxDim * (ratioH / ratioW) : maxDim;
		} else {
			h = maxDim;
			w = ratioH > 0 ? maxDim * (ratioW / ratioH) : maxDim;
		}
		return { x: (viewBox - w) / 2, y: (viewBox - h) / 2, w, h };
	}

	function formatDims(res: string): string {
		const parsed = parseResolution(res);
		return parsed ? `${parsed.width}×${parsed.height}` : res;
	}

	// --- Boxes-mode picker panel -------------------------------------------

	function normalizeOpt(opt: ResolutionOption): ResolutionOptionObject {
		return typeof opt === 'string' ? { value: opt } : opt;
	}

	$: normalizedOptions = options.map(normalizeOpt);
	$: filteredOptions = filterResolutionOptions(normalizedOptions, searchQuery);
	$: tierSections = groupOptionsByTier(filteredOptions);
	$: flatRows = tierSections.flatMap((s) => s.options);
	// Cumulative row count before each section, so a row's position in the
	// rendered (sectioned) markup maps onto one flat keyboard-nav index.
	$: sectionOffsets = (() => {
		const offsets: number[] = [];
		let acc = 0;
		for (const section of tierSections) {
			offsets.push(acc);
			acc += section.options.length;
		}
		return offsets;
	})();

	$: selectedOpt = normalizedOptions.find((o) => o.value === localValue) ?? null;
	$: triggerInfo = localValue ? getResolutionInfo(localValue) : null;
	$: triggerRatioLabel = (selectedOpt && getOptRatio(selectedOpt)) || triggerInfo?.ratio || null;
	$: triggerTierLabel = selectedOpt ? optionTier(selectedOpt) : null;

	async function openPanel() {
		if (disabled || panelOpen) return;
		panelOpen = true;
		searchQuery = '';
		customMode = false;
		customError = null;
		customWidth = '';
		customHeight = '';
		activeRowIndex = 0;
		await tick();
		updatePanelPosition();
		searchInputRef?.focus();
	}

	function closePanel() {
		panelOpen = false;
		customMode = false;
	}

	function handleTriggerClick() {
		if (disabled) return;
		if (panelOpen) {
			closePanel();
		} else {
			openPanel();
		}
	}

	function updatePanelPosition() {
		if (!triggerRef) return;
		const rect = triggerRef.getBoundingClientRect();
		const maxHeight = 360;
		const gap = 4;
		const viewportHeight = window.innerHeight;
		const spaceBelow = viewportHeight - rect.bottom;
		const spaceAbove = rect.top;
		panelPosition = {
			top: rect.bottom + gap,
			bottom: viewportHeight - rect.top + gap,
			left: rect.left,
			width: Math.max(rect.width, 300),
			openUpward: spaceBelow < maxHeight && spaceAbove > spaceBelow
		};
	}

	function rowIndex(sectionIndex: number, optIndex: number): number {
		return (sectionOffsets[sectionIndex] ?? 0) + optIndex;
	}

	function scrollActiveRowIntoView() {
		if (!panelRef) return;
		const el = panelRef.querySelector<HTMLElement>(`[data-row-index="${activeRowIndex}"]`);
		el?.scrollIntoView({ block: 'nearest' });
	}

	function openCustomMode() {
		customMode = true;
		customError = null;
		tick().then(() => customWidthRef?.focus());
	}

	function cancelCustomMode() {
		customMode = false;
		customError = null;
		tick().then(() => searchInputRef?.focus());
	}

	function applyCustom() {
		const result = parseCustomResolution(customWidth, customHeight);
		if (!result.ok || !result.value) {
			customError = result.error ?? 'Invalid resolution.';
			return;
		}
		handleSelect(result.value);
	}

	function handleCustomKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			applyCustom();
		} else if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			cancelCustomMode();
		}
	}

	function handleSearchKeydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown') {
			event.preventDefault();
			activeRowIndex = Math.min(activeRowIndex + 1, flatRows.length);
			scrollActiveRowIntoView();
		} else if (event.key === 'ArrowUp') {
			event.preventDefault();
			activeRowIndex = Math.max(activeRowIndex - 1, 0);
			scrollActiveRowIntoView();
		} else if (event.key === 'Enter') {
			event.preventDefault();
			if (activeRowIndex >= flatRows.length) {
				openCustomMode();
			} else {
				const opt = flatRows[activeRowIndex];
				if (opt) handleSelect(opt.value);
			}
		}
		// Escape is handled by the window-level listener below, which also
		// covers Escape pressed anywhere else in the panel.
	}

	function handleWindowPointerDown(event: PointerEvent) {
		if (!panelOpen) return;
		const target = event.target as Node;
		if (triggerRef?.contains(target) || panelRef?.contains(target)) return;
		closePanel();
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (panelOpen && event.key === 'Escape') {
			event.preventDefault();
			closePanel();
			triggerRef?.focus();
		}
	}

	// The panel is anchored to the trigger in viewport coordinates (it's
	// portalled to <body> - see the BaseModal containing-block note in
	// GenerationDetailsModal.svelte), so any scroll or resize of an ancestor
	// (the form pane this field lives in scrolls) must re-anchor it.
	function handleReposition() {
		if (panelOpen) updatePanelPosition();
	}

	if (typeof window !== 'undefined') {
		window.addEventListener('pointerdown', handleWindowPointerDown, true);
		window.addEventListener('keydown', handleWindowKeydown);
		window.addEventListener('scroll', handleReposition, true);
		window.addEventListener('resize', handleReposition);
	}

	onDestroy(() => {
		if (typeof window === 'undefined') return;
		window.removeEventListener('pointerdown', handleWindowPointerDown, true);
		window.removeEventListener('keydown', handleWindowKeydown);
		window.removeEventListener('scroll', handleReposition, true);
		window.removeEventListener('resize', handleReposition);
	});
</script>

<div class="field-card">
	<div class="flex items-center justify-between mb-1">
		<label id={name ? `${name}-label` : undefined} class="label !mb-0">
			{label}
			{#if tooltip}
				<Tooltip text={tooltip} position="top">
					<span class="ml-1 text-fg-subtle cursor-help inline-flex items-center">
						<Icon name="info" className="w-3.5 h-3.5" />
					</span>
				</Tooltip>
			{/if}
		</label>
	</div>

	{#if description}
		<p id={name ? `${name}-desc` : undefined} class="text-xs text-fg-muted mb-1">{description}</p>
	{/if}

	<!-- Sunken trigger + a searchable, tier-grouped floating panel. -->
		<button
			type="button"
			bind:this={triggerRef}
			on:click={handleTriggerClick}
			disabled={disabled}
			aria-haspopup="listbox"
			aria-expanded={panelOpen}
			class="input flex items-center gap-2.5 text-left hover:bg-surface-3 {panelOpen
				? 'ring-2 ring-accent border-transparent'
				: ''} {disabled ? 'opacity-50 cursor-not-allowed hover:bg-field-bg' : 'cursor-pointer'}"
		>
			{#if triggerInfo}
				{@const [rw, rh] = getRatioNums(selectedOpt ?? { value: localValue }, triggerInfo)}
				{@const g = glyphBox(rw, rh, 14)}
				<svg viewBox="0 0 24 24" class="w-4 h-4 flex-shrink-0 text-signal" fill="none" stroke="currentColor">
					<rect x={g.x} y={g.y} width={g.w} height={g.h} stroke-width="1.5" rx="1.5" />
				</svg>
				<span class="font-mono text-sm font-semibold tabular-nums text-fg">{formatDims(localValue)}</span>
				{#if triggerRatioLabel}
					<span class="font-mono text-2xs tabular-nums text-fg-subtle">{triggerRatioLabel}</span>
				{/if}
				{#if triggerTierLabel}
					<Badge variant="signal" size="sm" class="ml-auto">{triggerTierLabel}</Badge>
				{/if}
			{:else}
				<span class="flex-1 text-sm text-fg-subtle">Select resolution…</span>
			{/if}
			<Icon
				name="chevron-down"
				className="w-4 h-4 text-fg-subtle flex-shrink-0 transition-transform duration-150 {panelOpen
					? 'rotate-180'
					: ''}"
			/>
		</button>

		{#if panelOpen}
			<div
				use:portal
				bind:this={panelRef}
				transition:fade={{ duration: 120 }}
				class="fixed z-[9999] flex flex-col bg-surface-1 border border-line-strong rounded-xl shadow-floating overflow-hidden"
				style="{panelPosition.openUpward
					? `bottom: ${panelPosition.bottom}px`
					: `top: ${panelPosition.top}px`}; left: {panelPosition.left}px; width: {panelPosition.width}px; max-height: 360px;"
				role="listbox"
				aria-label="{label || 'Resolution'} options"
			>
				<div class="p-2 border-b border-line flex-shrink-0">
					<div class="relative">
						<Icon
							name="search"
							className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-fg-subtle pointer-events-none"
						/>
						<input
							type="text"
							bind:this={searchInputRef}
							bind:value={searchQuery}
							on:keydown={handleSearchKeydown}
							placeholder="Search ratio or size…"
							class="w-full pl-8 pr-2 py-1.5 bg-canvas border border-line-strong rounded shadow-well text-sm text-fg placeholder-fg-subtle focus:outline-none focus:border-signal"
						/>
					</div>
				</div>

				<div class="flex-1 min-h-0 overflow-y-auto py-1">
					{#if tierSections.length === 0}
						<div class="px-3 py-6 text-center text-xs text-fg-subtle">No resolutions match "{searchQuery}"</div>
					{:else}
						{#each tierSections as section, si (section.tier)}
							<div
								class="px-3 py-1 font-mono text-2xs font-semibold uppercase tracking-[0.1em] text-fg-subtle bg-surface-1 sticky top-0 z-10"
							>
								{section.tier}
							</div>
							{#each section.options as opt, oi (opt.value)}
								{@const idx = rowIndex(si, oi)}
								{@const info = getResolutionInfo(opt.value)}
								{#if info}
									{@const isSelected = opt.value === localValue}
									{@const isActive = idx === activeRowIndex}
									{@const [rw, rh] = getRatioNums(opt, info)}
									{@const g = glyphBox(rw, rh, 14)}
									<button
										type="button"
										data-row-index={idx}
										role="option"
										aria-selected={isSelected}
										on:click={() => handleSelect(opt.value)}
										on:mouseenter={() => (activeRowIndex = idx)}
										class="w-full flex items-center gap-2.5 px-3 py-1.5 text-left border-l-2 transition-colors {isSelected
											? 'border-signal bg-gradient-to-b from-signal/[0.16] to-signal/[0.05]'
											: isActive
												? 'border-transparent bg-surface-2'
												: 'border-transparent hover:bg-surface-2'}"
									>
										<svg
											viewBox="0 0 24 24"
											class="w-3.5 h-3.5 flex-shrink-0 {isSelected ? 'text-signal' : 'text-fg-subtle'}"
											fill="none"
											stroke="currentColor"
										>
											<rect x={g.x} y={g.y} width={g.w} height={g.h} stroke-width="1.5" rx="1.5" />
										</svg>
										<span
											class="flex-1 min-w-0 truncate text-xs {isSelected
												? 'text-signal font-medium'
												: 'text-fg-muted'}">{getOptDescription(opt) || opt.value}</span
										>
										<span class="font-mono text-2xs tabular-nums {isSelected ? 'text-signal' : 'text-fg-subtle'}"
											>{formatDims(opt.value)}</span
										>
									</button>
								{/if}
							{/each}
						{/each}
					{/if}

					<div class="px-1.5 pt-1">
						{#if !customMode}
							<button
								type="button"
								data-row-index={flatRows.length}
								on:click={openCustomMode}
								on:mouseenter={() => (activeRowIndex = flatRows.length)}
								class="w-full flex items-center gap-2 px-2.5 py-1.5 rounded border border-dashed transition-colors {activeRowIndex ===
								flatRows.length
									? 'border-line-hover bg-surface-2 text-fg'
									: 'border-line-strong text-fg-subtle hover:text-fg hover:border-line-hover'}"
							>
								<Icon name="plus" className="w-3.5 h-3.5" />
								<span class="font-mono text-2xs">Custom size…</span>
							</button>
						{:else}
							<div class="flex flex-col gap-1.5 px-1 py-1">
								<div class="flex items-center gap-1.5">
									<input
										type="text"
										inputmode="numeric"
										bind:this={customWidthRef}
										bind:value={customWidth}
										on:keydown={handleCustomKeydown}
										placeholder="768"
										aria-label="Custom width"
										class="w-16 font-mono text-xs tabular-nums text-right bg-canvas border border-line-strong rounded shadow-well px-2 py-1 text-fg focus:outline-none focus:border-signal"
									/>
									<span class="text-fg-subtle text-xs">×</span>
									<input
										type="text"
										inputmode="numeric"
										bind:value={customHeight}
										on:keydown={handleCustomKeydown}
										placeholder="768"
										aria-label="Custom height"
										class="w-16 font-mono text-xs tabular-nums bg-canvas border border-line-strong rounded shadow-well px-2 py-1 text-fg focus:outline-none focus:border-signal"
									/>
									<Button variant="primary" size="xs" class="ml-auto" onclick={applyCustom}>Apply</Button>
									<IconButton icon="close" label="Cancel custom size" size="sm" onclick={cancelCustomMode} />
								</div>
								{#if customError}
									<span class="text-2xs text-danger">{customError}</span>
								{/if}
							</div>
						{/if}
					</div>
				</div>
			</div>
		{/if}
</div>
