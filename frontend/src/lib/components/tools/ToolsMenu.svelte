<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Kbd } from '$lib/components/ui';
	import { getIconPath } from '$lib/utils/IconLibrary';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';
	import {
		toolLabel,
		type MediaTool,
		type MediaToolContext,
		type MediaToolGroup,
		type MediaToolScope,
		type ToolRunExtra
	} from '$lib/tools/tools';

	let {
		open,
		scope,
		groups,
		onToggle,
		onClose,
		onPick,
		placement = 'up',
		align = 'right',
		compact = false,
		icon = 'wand',
		variant = 'bar',
		ctx = null,
		anchorPoint = null,
		sheet = false,
		header = '',
		title = '',
		subtitle = '',
		rating = 0,
		collections = [],
		chipSize = 26,
		triggerClass: triggerClassOverride = ''
	}: {
		open: boolean;
		scope: MediaToolScope;
		groups: MediaToolGroup[];
		onToggle: () => void;
		onClose: () => void;
		onPick: (tool: MediaTool, extra?: ToolRunExtra) => void;
		placement?: 'up' | 'down';
		align?: 'left' | 'right';
		compact?: boolean;
		icon?: string;
		variant?: 'bar' | 'field' | 'entry';
		ctx?: MediaToolContext | null;
		anchorPoint?: { x: number; y: number } | null;
		sheet?: boolean;
		header?: string;
		title?: string;
		subtitle?: string;
		rating?: number;
		collections?: Array<{ id: string; name: string }>;
		chipSize?: number;
		triggerClass?: string;
	} = $props();

	const entryLabel = 'Item actions';
	const starPath = getIconPath('star') as string;
	const MENU_WIDTH = 256;
	const MENU_HEIGHT_ESTIMATE = 320;

	let trigger: HTMLDivElement | undefined = $state();
	let menuEl: HTMLDivElement | undefined = $state();
	let position = $state<FlippedMenuPosition>({ left: 0, top: 0, maxHeight: 320 });
	let submenu = $state<'collection' | null>(null);

	let positionStyle = $derived(
		sheet
			? ''
			: `left: ${position.left}px; ${position.top !== undefined ? `top: ${position.top}px;` : `bottom: ${position.bottom}px;`} max-height: ${position.maxHeight}px; width: ${MENU_WIDTH}px;`
	);

	function anchorElement(): HTMLElement | undefined {
		if (anchorPoint) {
			const { x, y } = anchorPoint;
			return {
				getBoundingClientRect: () => ({
					top: y,
					bottom: y,
					left: x,
					right: x,
					width: 0,
					height: 0,
					x,
					y,
					toJSON: () => ({})
				}),
				closest: () => null
			} as unknown as HTMLElement;
		}
		return trigger;
	}

	function reposition() {
		const anchor = anchorElement();
		if (!anchor || sheet) return;
		position = computeFlippedMenuPosition(anchor, {
			width: MENU_WIDTH,
			heightEstimate: menuEl?.getBoundingClientRect().height || MENU_HEIGHT_ESTIMATE,
			align: anchorPoint ? 'left' : align,
			preferred: anchorPoint ? 'down' : placement
		});
	}

	$effect(() => {
		if (!open) {
			submenu = null;
			return;
		}
		reposition();
		requestAnimationFrame(() => {
			reposition();
			focusItem(0);
		});
	});

	function items(): HTMLButtonElement[] {
		return menuEl ? Array.from(menuEl.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')) : [];
	}

	function focusItem(index: number) {
		const all = items();
		if (all.length === 0) return;
		all[(index + all.length) % all.length]?.focus();
	}

	function acceleratorTarget(key: string): MediaTool | null {
		for (const group of groups) {
			for (const { tool, availability } of group.tools) {
				const shortcut = tool.shortcut;
				if (!shortcut || shortcut === 'Enter' || !availability.enabled) continue;
				if (shortcut.toLowerCase() === key.toLowerCase()) return tool;
			}
		}
		return null;
	}

	function handleKeydown(event: KeyboardEvent) {
		if (!open) return;
		if (event.key === 'Escape') {
			if (submenu) {
				submenu = null;
				return;
			}
			onClose();
			(trigger?.querySelector('button') as HTMLButtonElement | null)?.focus();
			return;
		}
		if (
			(variant === 'entry' || variant === 'bar') &&
			!event.ctrlKey &&
			!event.metaKey &&
			!event.altKey &&
			!submenu
		) {
			const target = acceleratorTarget(event.key);
			if (target) {
				event.preventDefault();
				pick(target, true);
				return;
			}
		}
		if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
		if (!menuEl?.contains(document.activeElement)) return;
		event.preventDefault();
		const all = items();
		const at = all.indexOf(document.activeElement as HTMLButtonElement);
		focusItem(at + (event.key === 'ArrowDown' ? 1 : -1));
	}

	function handlePointerDown(event: PointerEvent) {
		if (!open) return;
		const target = event.target as Node;
		if (trigger?.contains(target) || menuEl?.contains(target)) return;
		onClose();
	}

	function handleViewportChange(event: Event) {
		if (!open || variant !== 'entry' || sheet) return;
		if (event.target instanceof Node && menuEl?.contains(event.target)) return;
		onClose();
	}

	function pick(tool: MediaTool, enabled: boolean, extra?: ToolRunExtra) {
		if (!enabled) return;
		onClose();
		if (extra === undefined) onPick(tool);
		else onPick(tool, extra);
	}

	function activate(tool: MediaTool, enabled: boolean) {
		if (!enabled) return;
		if (tool.control === 'collection') {
			submenu = 'collection';
			requestAnimationFrame(() => focusItem(0));
			return;
		}
		pick(tool, enabled);
	}

	function hintFor(tool: MediaTool, enabled: boolean, reason: string | undefined): string {
		return (enabled ? tool.description : reason) ?? '';
	}

	function labelOf(tool: MediaTool): string {
		return ctx ? toolLabel(tool, ctx) : tool.label;
	}

	function shortcutKeys(shortcut: string): string[] {
		if (shortcut === 'Enter') return ['↵'];
		if (shortcut === 'Backspace') return ['⌫'];
		return shortcut.split(' ');
	}

	let triggerClass = $derived(
		variant === 'entry'
			? `relative inline-flex items-center justify-center rounded border border-line-strong bg-surface-2 text-fg shadow-raised hover:border-line-hover hover:bg-surface-3 transition-[opacity,background-color,border-color] duration-100 before:absolute before:-inset-[6px] before:content-[''] ${open ? 'ring-1 ring-signal opacity-100' : ''} ${triggerClassOverride}`
			: variant === 'field'
				? `inline-flex items-center justify-center gap-1.5 h-8 rounded border border-line-strong bg-surface-2 text-sm text-fg hover:border-line-hover hover:bg-surface-3 transition-colors ${compact ? 'w-8' : 'px-2.5'}`
				: 'px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5'
	);

	let rowPad = $derived(sheet ? 'py-3 text-base' : 'py-1.5 text-sm');
</script>

<svelte:window
	onkeydown={handleKeydown}
	onpointerdown={handlePointerDown}
	onscrollcapture={handleViewportChange}
	onresize={handleViewportChange}
/>

<div class="relative" data-tools-menu-scope={scope} bind:this={trigger}>
	{#if variant === 'entry'}
		<button
			type="button"
			class={triggerClass}
			style="width: {chipSize}px; height: {chipSize}px"
			aria-haspopup="menu"
			aria-expanded={open}
			aria-label={entryLabel}
			data-entry-menu-trigger
			onclick={(event) => {
				event.stopPropagation();
				onToggle();
			}}
		>
			<Icon name="more" className="rotate-90 {chipSize <= 22 ? 'w-3 h-3' : 'w-4 h-4'}" />
		</button>
	{:else if compact}
		<Tooltip text="Tools" position="top">
			<button
				type="button"
				class={triggerClass}
				aria-haspopup="menu"
				aria-expanded={open}
				aria-label="Tools"
				data-tools-trigger
				onclick={onToggle}
			>
				<Icon name={icon} className="w-4 h-4" />
			</button>
		</Tooltip>
	{:else}
		<button
			type="button"
			class={triggerClass}
			aria-haspopup="menu"
			aria-expanded={open}
			data-tools-trigger
			onclick={onToggle}
		>
			<Icon name="wand" className="w-4 h-4" />
			Tools
			<Icon name={placement === 'up' ? 'chevron-up' : 'chevron-down'} className="w-3 h-3" />
		</button>
	{/if}
</div>

{#snippet rows()}
	{#if submenu === 'collection'}
		<button
			type="button"
			role="menuitem"
			class="w-full px-3 {rowPad} text-left rounded flex items-center gap-2 text-fg-muted hover:text-fg hover:bg-surface-2 focus-visible:bg-surface-2"
			data-submenu-back
			onclick={() => (submenu = null)}
		>
			<Icon name="chevron-left" className="w-4 h-4 flex-shrink-0" />
			<span class="flex-1 min-w-0 truncate">Add to collection</span>
		</button>
		{#each collections as collection (collection.id)}
			<button
				type="button"
				role="menuitem"
				class="w-full px-3 {rowPad} text-left rounded flex items-center gap-2 text-fg-muted hover:text-fg hover:bg-surface-2 focus-visible:bg-surface-2"
				data-collection-id={collection.id}
				onclick={() => {
					const tool = groups.flatMap((group) => group.tools).find((entry) => entry.tool.control === 'collection');
					if (tool) pick(tool.tool, true, { collectionId: collection.id });
				}}
			>
				<Icon name="folder" className="w-4 h-4 flex-shrink-0" />
				<span class="flex-1 min-w-0 truncate">{collection.name}</span>
			</button>
		{/each}
		{#if collections.length === 0}
			<p class="px-3 py-2 text-sm text-fg-subtle">No collections yet.</p>
		{/if}
	{:else}
		{#if header}
			<span
				class="px-3 pt-2 pb-1 font-mono text-xs uppercase tracking-[0.07em] text-signal"
				data-menu-header
			>
				{header}
			</span>
		{/if}
		{#each groups as group (group.category.id)}
			{@const isDanger = group.category.id === 'danger'}
			<div
				role="group"
				aria-label={group.category.label}
				class="flex flex-col {isDanger ? 'mt-1 pt-1 border-t border-line' : ''}"
			>
				{#if !isDanger}
					<span class="px-3 pt-2 pb-1 font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">
						{group.category.label}
					</span>
				{/if}
				{#each group.tools as { tool, availability } (tool.id)}
					{@const hint = hintFor(tool, availability.enabled, availability.reason)}
					{@const inlineReason = (scope === 'field' || variant === 'entry') && !availability.enabled}
					{#if tool.control === 'rating'}
						<div class="flex items-center gap-2 px-3 {rowPad} text-fg-muted" data-tool={tool.id}>
							<Icon name={tool.icon} className="w-4 h-4 flex-shrink-0" />
							<span class="flex-1 min-w-0 truncate">{labelOf(tool)}</span>
							<div class="flex items-center">
								{#each [1, 2, 3, 4, 5] as star (star)}
									<button
										type="button"
										role="menuitem"
										class="p-0.5 rounded focus-visible:bg-surface-2 {star <= rating ? 'text-signal' : 'text-line-hover hover:text-fg-muted'}"
										aria-label="Rate {star} of 5"
										data-rating={star}
										onclick={() => pick(tool, true, { rating: star === rating ? 0 : star })}
									>
										<svg
											class="w-3.5 h-3.5"
											viewBox="0 0 24 24"
											fill={star <= rating ? 'currentColor' : 'none'}
											stroke="currentColor"
											stroke-width="1.5"
										>
											<path stroke-linecap="round" stroke-linejoin="round" d={starPath} />
										</svg>
									</button>
								{/each}
							</div>
						</div>
					{:else}
						<Tooltip text={inlineReason ? '' : hint} position="right" wrapperClass="flex w-full">
							<button
								type="button"
								role="menuitem"
								class="w-full px-3 {rowPad} text-left rounded transition-colors flex items-center gap-2 {availability.enabled
									? tool.tone === 'danger'
										? 'text-danger hover:bg-danger/10 focus-visible:bg-danger/10'
										: 'text-fg-muted hover:text-fg hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:text-fg'
									: 'text-fg-muted opacity-40 cursor-not-allowed'}"
								aria-disabled={!availability.enabled}
								data-tool={tool.id}
								onclick={() => activate(tool, availability.enabled)}
							>
								<Icon name={tool.icon} className="w-4 h-4 flex-shrink-0" />
								<span class="flex-1 min-w-0 truncate">{labelOf(tool)}</span>
								{#if inlineReason && availability.reason}
									<span class="shrink-0 font-mono text-xs text-fg-subtle">{availability.reason}</span>
								{:else if tool.control === 'collection'}
									<Icon name="chevron-right" className="w-3.5 h-3.5 flex-shrink-0 text-fg-subtle" />
								{:else if tool.source === 'plugin' && variant === 'entry'}
									<span class="shrink-0 px-1 font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle border border-line rounded-sm">
										Plugin
									</span>
								{:else if availability.enabled && tool.shortcut && !sheet}
									<Kbd keys={shortcutKeys(tool.shortcut)} size="md" />
								{/if}
							</button>
						</Tooltip>
					{/if}
				{/each}
			</div>
		{/each}

		{#if groups.length === 0}
			<p class="px-3 py-2 text-sm text-fg-subtle">No tools for this selection.</p>
		{/if}
	{/if}
{/snippet}

{#if open}
	{#if sheet}
		<div use:portal use:overlayLayer class="fixed inset-0 flex flex-col justify-end" data-tools-sheet-root>
			<div class="absolute inset-0 bg-black/60" aria-hidden="true"></div>
			<div
				bind:this={menuEl}
				class="relative max-h-[80dvh] overflow-y-auto bg-surface-1 border-t border-line-strong rounded-t-xl shadow-overlay p-2 flex flex-col"
				role="menu"
				data-tools-menu
				data-tools-sheet
				data-tools-menu-scope={scope}
			>
				<div class="mx-auto mt-1 mb-2 h-1 w-9 rounded bg-line-strong" aria-hidden="true"></div>
				{#if title}
					<div class="flex items-center gap-3 px-3 pb-2">
						<div class="min-w-0 flex-1">
							<p class="font-mono tabular-nums text-sm text-fg truncate">{title}</p>
							{#if subtitle}
								<p class="font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle truncate">{subtitle}</p>
							{/if}
						</div>
						<button
							type="button"
							class="p-2 rounded text-fg-muted hover:text-fg hover:bg-surface-2"
							aria-label="Close menu"
							onclick={onClose}
						>
							<Icon name="close" className="w-4 h-4" />
						</button>
					</div>
				{/if}
				{@render rows()}
			</div>
		</div>
	{:else}
		<div
			use:portal
			use:overlayLayer
			bind:this={menuEl}
			class="fixed z-overlay overflow-y-auto bg-surface-1 border border-line-strong rounded-xl shadow-overlay p-1 flex flex-col"
			style={positionStyle}
			role="menu"
			data-tools-menu
			data-tools-menu-scope={scope}
		>
			{@render rows()}
		</div>
	{/if}
{/if}
