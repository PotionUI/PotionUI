<script lang="ts">
	import { tick, type Snippet } from 'svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import dismiss from '$lib/actions/dismiss';
	import { trackMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';

	let {
		label,
		open = $bindable(false),
		width = 384,
		align = 'left',
		trigger,
		children
	}: {
		label: string;
		open?: boolean;
		width?: number;
		align?: 'left' | 'right';
		trigger: Snippet<[{ open: boolean; toggle: () => void }]>;
		children: Snippet<[{ close: () => void }]>;
	} = $props();

	let triggerEl = $state<HTMLElement>();
	let panelEl = $state<HTMLElement>();
	let position = $state<FlippedMenuPosition | null>(null);
	let focused = false;

	function toggle() {
		open = !open;
	}

	function close() {
		if (!open) return;
		open = false;
		position = null;
		focused = false;
		void tick().then(() => triggerEl?.querySelector<HTMLElement>('button')?.focus());
	}

	$effect(() => {
		if (!open || !triggerEl || !panelEl) return;
		const panel = panelEl;
		return trackMenuPosition(
			triggerEl,
			() => ({ width: Math.min(width, window.innerWidth - 16), heightEstimate: panel.offsetHeight, gap: 6, align }),
			(next) => {
				const bounds = triggerEl?.closest('[aria-modal="true"]')?.getBoundingClientRect();
				const fitted = Math.min(width, window.innerWidth - 16);
				position = bounds ? { ...next, left: Math.max(8, Math.min(next.left, bounds.right - fitted - 12)) } : next;
			}
		);
	});

	$effect(() => {
		if (!open || !panelEl || !position || focused) return;
		focused = true;
		const panel = panelEl;
		void tick().then(() => panel.querySelector<HTMLElement>('select, input, button')?.focus());
	});

	function style(pos: FlippedMenuPosition | null): string {
		const w = `width: ${width}px; max-width: calc(100vw - 16px);`;
		if (!pos) return `visibility: hidden; top: 0; left: 0; ${w}`;
		const vertical = pos.top !== undefined ? `top: ${pos.top}px;` : `bottom: ${pos.bottom}px;`;
		return `${vertical} left: ${pos.left}px; max-height: ${pos.maxHeight}px; ${w}`;
	}
</script>

<span bind:this={triggerEl} class="inline-flex">
	{@render trigger({ open, toggle })}
</span>

{#if open}
	<div
		bind:this={panelEl}
		use:portal
		use:overlayLayer
		use:dismiss={{ onDismiss: () => close(), ignore: [triggerEl] }}
		role="dialog"
		aria-label={label}
		data-picker-popover
		class="fixed flex flex-col overflow-hidden rounded-xl border border-line-strong bg-surface-2 shadow-floating"
		style={style(position)}
	>
		{@render children({ close })}
	</div>
{/if}
