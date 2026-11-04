<script module lang="ts">
	export interface MenuItem {
		id: string;
		label: string;
		icon?: string;
		danger?: boolean;
		disabled?: boolean;
		separator?: boolean;
	}
</script>

<script lang="ts">
	import { tick, type Snippet } from 'svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import dismiss from '$lib/actions/dismiss';
	import menuNav, { focusFirstItem } from '$lib/actions/menuNav';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import IconButton from './IconButton.svelte';
	import { trackMenuPosition, type FlippedMenuPosition, type MenuAnchor } from '$lib/utils/menuPosition';

	let {
		label,
		menuLabel,
		tooltip,
		icon = 'more',
		size = 'sm',
		items = [],
		onselect,
		open = $bindable(false),
		width = 220,
		align = 'right',
		preferred = 'down',
		gap = 4,
		menuAttrs = {},
		anchor = null,
		onopen,
		onclose,
		onescape,
		trigger,
		children
	}: {
		label: string;
		menuLabel?: string;
		tooltip?: string;
		icon?: string;
		size?: 'xs' | 'sm' | 'md';
		items?: MenuItem[];
		onselect?: (id: string) => void;
		open?: boolean;
		width?: number;
		align?: 'left' | 'right';
		preferred?: 'up' | 'down';
		gap?: number;
		menuAttrs?: Record<string, string | number | boolean | undefined>;
		anchor?: MenuAnchor | null;
		onopen?: () => void;
		onclose?: () => void;
		onescape?: () => boolean;
		trigger?: Snippet<[{ open: boolean; toggle: () => void }]>;
		children?: Snippet<[{ close: (restoreFocus?: boolean) => void }]>;
	} = $props();

	let triggerEl = $state<HTMLElement>();
	let menuEl = $state<HTMLElement>();
	let position = $state<FlippedMenuPosition | null>(null);
	let focused = false;

	function toggle() {
		if (open) close();
		else {
			open = true;
			onopen?.();
		}
	}

	function close(restoreFocus = false) {
		if (!open) return;
		open = false;
		position = null;
		focused = false;
		onclose?.();
		if (restoreFocus) {
			void tick().then(() =>
				triggerEl?.querySelector<HTMLElement>('button, [tabindex]')?.focus()
			);
		}
	}

	function choose(id: string) {
		close(true);
		onselect?.(id);
	}

	function menuStyle(pos: FlippedMenuPosition | null): string {
		if (!pos) return `visibility: hidden; top: 0; left: 0; width: ${width}px;`;
		const vertical = pos.top !== undefined ? `top: ${pos.top}px;` : `bottom: ${pos.bottom}px;`;
		return `${vertical} left: ${pos.left}px; width: ${width}px; max-height: ${pos.maxHeight}px;`;
	}

	$effect(() => {
		if (!open || !triggerEl || !menuEl) return;
		const menu = menuEl;
		return trackMenuPosition(
			anchor ?? triggerEl,
			() => ({ width, heightEstimate: menu.offsetHeight, gap, align, preferred }),
			(next) => (position = next)
		);
	});

	$effect(() => {
		if (!open || !menuEl || !position || focused) return;
		focused = true;
		const menu = menuEl;
		void tick().then(() => focusFirstItem(menu));
	});
</script>

<span bind:this={triggerEl} class="inline-flex">
	{#snippet triggerBody()}
		{#if trigger}
			{@render trigger({ open, toggle })}
		{:else}
			<IconButton {icon} {label} {size} ariaExpanded={open} onclick={toggle} />
		{/if}
	{/snippet}
	{#if tooltip}
		<Tooltip text={tooltip} position="left" delay={150}>
			{@render triggerBody()}
		</Tooltip>
	{:else}
		{@render triggerBody()}
	{/if}
</span>

{#if open}
	<div
		bind:this={menuEl}
		use:portal
		use:overlayLayer
		use:dismiss={{ onDismiss: (reason) => (reason === 'escape' && onescape?.() ? undefined : close(reason === 'escape')), ignore: [triggerEl] }}
		use:menuNav
		role="menu"
		aria-label={menuLabel ?? label}
		class="fixed overflow-y-auto rounded-xl border border-line-strong bg-surface-2 p-1 shadow-floating"
		style={menuStyle(position)}
		{...menuAttrs}
	>
		{#if children}
			{@render children({ close })}
		{:else}
			{#each items as item (item.id)}
				{#if item.separator}
					<div class="my-1 h-px bg-line" role="separator"></div>
				{:else}
					<button
						type="button"
						role="menuitem"
						disabled={item.disabled}
						class="flex h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-sm hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 {item.danger ? 'text-danger' : 'text-fg'}"
						onclick={() => choose(item.id)}
					>
						{#if item.icon}
							<Icon name={item.icon} className="h-4 w-4 flex-shrink-0" />
						{/if}
						<span class="min-w-0 flex-1 truncate">{item.label}</span>
					</button>
				{/if}
			{/each}
		{/if}
	</div>
{/if}
