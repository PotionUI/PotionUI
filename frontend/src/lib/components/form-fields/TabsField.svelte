<script lang="ts">
	import { onMount, onDestroy, getContext } from 'svelte';
	import { writable, type Writable } from 'svelte/store';
	import FieldChildren from './FieldChildren.svelte';
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';
	import { FORM_FIELD_ERRORS_CONTEXT_KEY } from '$lib/form/fieldErrorsContext';

	export let name: string | null;
	export let config: any;
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	export let onOriginChange: ((fieldName: string, origin: unknown) => void) | undefined = undefined;
	export let onMaskChange: ((fieldName: string, maskPath: string | undefined) => void) | undefined = undefined;
	export let fieldPath: string | undefined = undefined;

	let activeTab = 0;

	// Falls back to an empty, never-updated store when rendered outside a
	// DynamicForm (e.g. in isolation/tests) so `$fieldErrorsStore` is always safe.
	const fieldErrorsStore =
		getContext<Writable<Record<string, string[]>>>(FORM_FIELD_ERRORS_CONTEXT_KEY) ??
		writable<Record<string, string[]>>({});

	// All field names nested (at any depth) under a tab, used both for the
	// per-tab error-count badge and to auto-switch to the first erroring tab.
	function collectFieldNames(node: any): string[] {
		const names: string[] = [];
		if (node?.name) names.push(node.name);
		if (Array.isArray(node?.children)) {
			for (const child of node.children) names.push(...collectFieldNames(child));
		}
		return names;
	}

	// Auto-switch to the first tab containing an erroring field whenever a new
	// batch of fieldErrors arrives (e.g. right after a failed submission) - but
	// never fight the user's own tab clicks for the same error batch.
	let lastAutoSwitchErrorKey = '';
	$: {
		const erroredNames = Object.keys($fieldErrorsStore).filter((n) => $fieldErrorsStore[n]?.length);
		const key = erroredNames.sort().join(',');
		if (key && key !== lastAutoSwitchErrorKey) {
			lastAutoSwitchErrorKey = key;
			const target = visibleTabEntries.find(({ tab }: { tab: any }) =>
				collectFieldNames(tab).some((n) => erroredNames.includes(n))
			);
			if (target && target.index !== activeTab) activeTab = target.index;
		} else if (!key) {
			lastAutoSwitchErrorKey = '';
		}
	}
	let tabsContainer: HTMLDivElement;
	let hasOverflow = false;
	let canScrollLeft = false;
	let canScrollRight = false;
	$: tabsLabel = config.title || config.label || name || 'Options';

	// Tabs never go through FormField's own visibility check (this component
	// iterates config.children directly), so a tab whose entire subtree the
	// audience filter hid (see src/lib/utils/audienceFilter.ts) must be
	// filtered out here instead - otherwise an all-advanced tab would still
	// render as an empty panel in Simple mode.
	$: visibleTabEntries = (config.children || [])
		.map((tab: any, index: number) => ({ tab, index }))
		.filter(({ tab }: { tab: any }) => tab.visible !== false);

	// Per-tab error-count badge, keyed by `index` (the `{#each}`s below are keyed by
	// `index`). This is a `$:` statement (not a plain function called from
	// `{@const}`) so Svelte's dependency scan sees `$fieldErrorsStore` directly —
	// calling a separately-declared function from `{@const}` hides that read and the
	// badge would freeze at its first-rendered count, never reflecting new/cleared
	// validation errors.
	$: errorCountByIndex = new Map<number, number>(
		visibleTabEntries.map(({ tab, index }: { tab: any; index: number }) => {
			const names = collectFieldNames(tab);
			const count = names.reduce(
				(acc, n) => (($fieldErrorsStore[n]?.length ?? 0) > 0 ? acc + 1 : acc),
				0
			);
			return [index, count] as [number, number];
		})
	);

	// Keep the active tab pointed at a visible one (e.g. right after the
	// Simple/Advanced toggle hides whichever tab is currently open).
	$: if (
		visibleTabEntries.length > 0 &&
		!visibleTabEntries.some(({ index }: { index: number }) => index === activeTab)
	) {
		activeTab = visibleTabEntries[0].index;
	}

	// Drag to scroll state
	let isDragging = false;
	let startX = 0;
	let scrollLeft = 0;

	function slugify(text: string | null | undefined): string {
		if (!text) return '';
		return text
			.toString()
			.toLowerCase()
			.trim()
			.replace(/\s+/g, '_')
			.replace(/[^\w-]+/g, '')
			.replace(/--+/g, '_');
	}

	// A `tab` entry never goes through FieldChildren's structural branch (this
	// component iterates config.children directly), so it appends its own
	// segment here using the same formula - see FieldChildren.svelte's
	// childFieldPath.
	function tabFieldPath(tab: any, index: number): string {
		const segment = tab.name || slugify(tab.label || tab.title) || String(index);
		return fieldPath ? `${fieldPath}/${segment}` : segment;
	}

	// Helper to render tab title with icon based on display mode
	function getTabDisplayMode(tab: any): 'icon_only' | 'icon_label' | 'label' {
		const icon = tab.configuration?.icon;
		const iconDisplay = tab.configuration?.icon_display;

		// If icon_display is explicitly set, use it
		if (iconDisplay) return iconDisplay;

		// Default to icon_label if icon exists, otherwise label
		return icon ? 'icon_label' : 'label';
	}

	// Helper to get tooltip text for a tab
	function getTabTooltip(tab: any, tabLabel: string, displayMode: string): string | undefined {
		// Explicit tooltip from configuration
		const explicitTooltip = tab.tooltip || tab.configuration?.tooltip;
		if (explicitTooltip) return explicitTooltip;

		// For icon_only mode, use the label as tooltip
		if (displayMode === 'icon_only') return tabLabel;

		// Otherwise no tooltip
		return undefined;
	}

	// Check if tabs overflow their container
	function checkOverflow() {
		if (!tabsContainer) return;
		hasOverflow = tabsContainer.scrollWidth > tabsContainer.clientWidth;
		updateScrollButtons();
	}

	// Update arrow button visibility
	function updateScrollButtons() {
		if (!tabsContainer) return;
		canScrollLeft = tabsContainer.scrollLeft > 0;
		canScrollRight = tabsContainer.scrollLeft < tabsContainer.scrollWidth - tabsContainer.clientWidth - 1;
	}

	// Scroll by arrow buttons
	function scrollTabs(direction: 'left' | 'right') {
		if (!tabsContainer) return;
		const scrollAmount = 150;
		tabsContainer.scrollBy({
			left: direction === 'left' ? -scrollAmount : scrollAmount,
			behavior: 'smooth'
		});
	}

	// Drag to scroll handlers
	function handleMouseDown(e: MouseEvent) {
		// Only initiate drag if clicking on the container background, not on buttons
		if ((e.target as HTMLElement).closest('button')) return;

		isDragging = true;
		startX = e.pageX - tabsContainer.offsetLeft;
		scrollLeft = tabsContainer.scrollLeft;
		tabsContainer.style.cursor = 'grabbing';
	}

	function handleMouseMove(e: MouseEvent) {
		if (!isDragging) return;
		e.preventDefault();
		const x = e.pageX - tabsContainer.offsetLeft;
		const walk = (x - startX) * 1.5; // Scroll speed multiplier
		tabsContainer.scrollLeft = scrollLeft - walk;
	}

	function handleMouseUp() {
		isDragging = false;
		if (tabsContainer) {
			tabsContainer.style.cursor = hasOverflow ? 'grab' : 'default';
		}
	}

	function handleMouseLeave() {
		if (isDragging) {
			isDragging = false;
			if (tabsContainer) {
				tabsContainer.style.cursor = hasOverflow ? 'grab' : 'default';
			}
		}
	}

	// Resize observer for dynamic overflow detection
	let resizeObserver: ResizeObserver;

	onMount(() => {
		checkOverflow();

		resizeObserver = new ResizeObserver(() => {
			checkOverflow();
		});

		if (tabsContainer) {
			resizeObserver.observe(tabsContainer);
			tabsContainer.addEventListener('scroll', updateScrollButtons);
		}
	});

	onDestroy(() => {
		if (resizeObserver) {
			resizeObserver.disconnect();
		}
		if (tabsContainer) {
			tabsContainer.removeEventListener('scroll', updateScrollButtons);
		}
	});
</script>

<div class="w-full">
	<!-- Tab buttons wrapper -->
	<div class="flex items-center border-b border-line px-2">
		<!-- Left arrow -->
		{#if hasOverflow && canScrollLeft}
			<button
				type="button"
				on:click={() => scrollTabs('left')}
				class="flex-shrink-0 px-1 py-2 text-fg-disabled hover:text-fg-subtle transition-colors"
				aria-label="Scroll tabs left"
			>
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
				</svg>
			</button>
		{/if}

		<!-- Tab buttons container -->
		<div
			bind:this={tabsContainer}
			on:mousedown={handleMouseDown}
			on:mousemove={handleMouseMove}
			on:mouseup={handleMouseUp}
			on:mouseleave={handleMouseLeave}
			class="flex-1 flex items-center justify-evenly overflow-x-hidden select-none -mb-px"
			class:cursor-grab={hasOverflow && !isDragging}
			class:cursor-grabbing={isDragging}
			role="tablist"
			aria-label={tabsLabel}
			tabindex="0"
		>
			{#if config.children}
				{#each visibleTabEntries as { tab, index } (index)}
					{@const tabLabel = tab.label || tab.title || `Tab ${index + 1}`}
					{@const iconName = tab.configuration?.icon}
					{@const displayMode = getTabDisplayMode(tab)}
					{@const tooltipText = getTabTooltip(tab, tabLabel, displayMode)}
					{@const errorCount = errorCountByIndex.get(index) ?? 0}

					{@const buttonClass = `px-3 py-2.5 text-sm font-medium transition-colors whitespace-nowrap border-b-2 flex items-center justify-center gap-1.5 ${activeTab === index
						? 'border-signal text-signal'
						: 'border-transparent text-fg-muted hover:text-fg hover:border-line-hover'}`}

					{#if tooltipText}
						<Tooltip text={tooltipText} position="top">
							<button type="button" on:click={() => (activeTab = index)} class={buttonClass} role="tab" aria-selected={activeTab === index} aria-label={tabLabel}>
								{#if iconName}
									<Icon name={iconName} className="w-4 h-4" />
								{/if}
								{#if displayMode !== 'icon_only'}
									<span>{tabLabel}</span>
								{/if}
								{#if errorCount > 0}
									<span class="inline-flex h-[15px] min-w-[15px] items-center justify-center rounded-full bg-danger/15 px-1 font-mono text-2xs text-danger">{errorCount}</span>
								{/if}
							</button>
						</Tooltip>
					{:else}
						<button type="button" on:click={() => (activeTab = index)} class={buttonClass} role="tab" aria-selected={activeTab === index} aria-label={tabLabel}>
							{#if iconName}
								<Icon name={iconName} className="w-4 h-4" />
							{/if}
							{#if displayMode !== 'icon_only'}
								<span>{tabLabel}</span>
							{:else if !iconName}
								<span class="font-medium">{tabLabel.charAt(0)}</span>
							{/if}
							{#if errorCount > 0}
								<span class="inline-flex h-[15px] min-w-[15px] items-center justify-center rounded-full bg-danger/15 px-1 font-mono text-2xs text-danger">{errorCount}</span>
							{/if}
						</button>
					{/if}
				{/each}
			{/if}
		</div>

		<!-- Right arrow -->
		{#if hasOverflow && canScrollRight}
			<button
				type="button"
				on:click={() => scrollTabs('right')}
				class="flex-shrink-0 px-1 py-2 text-fg-disabled hover:text-fg-subtle transition-colors"
				aria-label="Scroll tabs right"
			>
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
				</svg>
			</button>
		{/if}
	</div>

	<!-- Tab panels - ALL kept mounted, just hidden when not active -->
	{#if config.children}
		{#each visibleTabEntries as { tab, index } (index)}
			{#if tab.children}
				<div
					class="pt-3 space-y-4 {activeTab === index ? '' : 'hidden'}"
					style="animation: {activeTab === index ? 'tabFadeIn 0.2s ease-out' : 'none'};"
					role="tabpanel"
					aria-hidden={activeTab !== index}
				>
					<fieldset class="border-0 p-0 m-0 min-w-0 space-y-4">
						<legend class="sr-only">{tab.label || tab.title || ''}</legend>
						<FieldChildren
							children={tab.children}
							{value}
							{onChange}
							{onOriginChange}
							{onMaskChange}
							location="tab"
							fieldPath={tabFieldPath(tab, index)}
						/>
					</fieldset>
				</div>
			{/if}
		{/each}
	{/if}
</div>

<style>
	@keyframes tabFadeIn {
		from {
			opacity: 0;
			transform: translateY(4px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}

	@media (prefers-reduced-motion: reduce) {
		@keyframes tabFadeIn {
			from { opacity: 1; transform: none; }
			to { opacity: 1; transform: none; }
		}
	}
</style>
