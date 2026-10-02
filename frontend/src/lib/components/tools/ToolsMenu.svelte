<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Kbd } from '$lib/components/ui';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';
	import type { MediaTool, MediaToolGroup, MediaToolScope } from '$lib/tools/tools';

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
		variant = 'bar'
	}: {
		open: boolean;
		scope: MediaToolScope;
		groups: MediaToolGroup[];
		onToggle: () => void;
		onClose: () => void;
		onPick: (tool: MediaTool) => void;
		placement?: 'up' | 'down';
		align?: 'left' | 'right';
		compact?: boolean;
		icon?: string;
		variant?: 'bar' | 'field';
	} = $props();

	const MENU_WIDTH = 256;
	const MENU_HEIGHT_ESTIMATE = 320;

	let trigger: HTMLDivElement | undefined = $state();
	let menuEl: HTMLDivElement | undefined = $state();
	let position = $state<FlippedMenuPosition>({ left: 0, top: 0, maxHeight: 320 });

	let positionStyle = $derived(
		`left: ${position.left}px; ${position.top !== undefined ? `top: ${position.top}px;` : `bottom: ${position.bottom}px;`} max-height: ${position.maxHeight}px; width: ${MENU_WIDTH}px;`
	);

	function reposition() {
		if (!trigger) return;
		position = computeFlippedMenuPosition(trigger, {
			width: MENU_WIDTH,
			heightEstimate: menuEl?.getBoundingClientRect().height || MENU_HEIGHT_ESTIMATE,
			align,
			preferred: placement
		});
	}

	$effect(() => {
		if (!open) return;
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

	function handleKeydown(event: KeyboardEvent) {
		if (!open) return;
		if (event.key === 'Escape') {
			onClose();
			(trigger?.querySelector('button') as HTMLButtonElement | null)?.focus();
			return;
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

	function pick(tool: MediaTool, enabled: boolean) {
		if (!enabled) return;
		onClose();
		onPick(tool);
	}

	function hintFor(tool: MediaTool, enabled: boolean, reason: string | undefined): string {
		return (enabled ? tool.description : reason) ?? '';
	}

	let triggerClass = $derived(
		variant === 'field'
			? `inline-flex items-center justify-center gap-1.5 h-8 rounded border border-line-strong bg-surface-2 text-sm text-fg hover:border-line-hover hover:bg-surface-3 transition-colors ${compact ? 'w-8' : 'px-2.5'}`
			: 'px-3 py-1.5 text-sm text-fg-muted hover:text-fg hover:bg-surface-2 rounded transition-colors flex items-center gap-1.5'
	);
</script>

<svelte:window onkeydown={handleKeydown} onpointerdown={handlePointerDown} />

<div class="relative" data-tools-menu-scope={scope} bind:this={trigger}>
	{#if compact}
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

{#if open}
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
		{#each groups as group (group.category.id)}
			<div role="group" aria-label={group.category.label} class="flex flex-col">
				<span class="px-3 pt-2 pb-1 font-mono text-xs uppercase tracking-[0.07em] text-fg-subtle">
					{group.category.label}
				</span>
				{#each group.tools as { tool, availability } (tool.id)}
					{@const hint = hintFor(tool, availability.enabled, availability.reason)}
					{@const inlineReason = scope === 'field' && !availability.enabled}
					<Tooltip text={inlineReason ? '' : hint} position="right" wrapperClass="flex w-full">
						<button
							type="button"
							role="menuitem"
							class="w-full px-3 py-1.5 text-sm text-left rounded transition-colors flex items-center gap-2 {availability.enabled
								? 'text-fg-muted hover:text-fg hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:text-fg'
								: 'text-fg-muted opacity-40 cursor-not-allowed'}"
							aria-disabled={!availability.enabled}
							data-tool={tool.id}
							onclick={() => pick(tool, availability.enabled)}
						>
							<Icon name={tool.icon} className="w-4 h-4 flex-shrink-0" />
							<span class="flex-1 min-w-0 truncate">{tool.label}</span>
							{#if inlineReason && availability.reason}
								<span class="shrink-0 font-mono text-xs text-fg-subtle">{availability.reason}</span>
							{:else if availability.enabled && tool.shortcut}
								<Kbd keys={tool.shortcut.split(' ')} size="md" />
							{/if}
						</button>
					</Tooltip>
				{/each}
			</div>
		{/each}

		{#if groups.length === 0}
			<p class="px-3 py-2 text-sm text-fg-subtle">No tools for this selection.</p>
		{/if}
	</div>
{/if}
