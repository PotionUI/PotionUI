<script lang="ts">
	import { onMount, type Snippet } from 'svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import focusTrap from '$lib/actions/focusTrap';
	import { effectiveKeepOpen, handleDrawerKeydown, readKeepOpenPref, writeKeepOpenPref } from './drawerBehavior';

	let {
		label,
		keepOpenKey,
		onClose,
		onEscape,
		searchEl = $bindable(undefined),
		header,
		children
	}: {
		label: string;
		keepOpenKey: string;
		onClose: () => void;
		onEscape?: () => boolean;
		searchEl?: HTMLInputElement;
		header: Snippet<[{ keepOpen: boolean; setKeepOpen: (next: boolean) => void }]>;
		children: Snippet<[{ keepOpen: boolean }]>;
	} = $props();

	let root = $state<HTMLElement>();
	let keepOpenPref = $state(false);
	let viewportWidth = $state(typeof window === 'undefined' ? 1440 : window.innerWidth);

	let keepOpen = $derived(effectiveKeepOpen(keepOpenPref, viewportWidth));

	onMount(() => {
		keepOpenPref = readKeepOpenPref(keepOpenKey);
	});

	function setKeepOpen(next: boolean) {
		keepOpenPref = next;
		writeKeepOpenPref(keepOpenKey, next);
	}

	function handleKeydown(event: KeyboardEvent) {
		handleDrawerKeydown(event, { root, searchEl, keepOpen, onEscape, onClose });
	}
</script>

<svelte:window onresize={() => (viewportWidth = window.innerWidth)} onkeydown={handleKeydown} />

{#if !keepOpen}
	<div class="drawer-scrim" use:portal use:overlayLayer role="presentation" onclick={onClose}></div>
{/if}

<div
	class="side-drawer flex flex-col overflow-hidden border border-line-strong bg-surface-1 shadow-overlay"
	role="dialog"
	tabindex="-1"
	aria-label={label}
	aria-modal={keepOpen ? undefined : 'true'}
	bind:this={root}
	use:portal
	use:overlayLayer
	use:focusTrap
>
	{@render header({ keepOpen, setKeepOpen })}
	{@render children({ keepOpen })}
</div>

<style>
	.drawer-scrim {
		position: fixed;
		inset: 0;
		bottom: var(--dock-height, 0px);
		background: rgb(0 0 0 / 0.5);
	}

	.side-drawer {
		position: fixed;
		top: 12px;
		right: 12px;
		bottom: calc(var(--dock-height, 0px) + 12px);
		--side-drawer-width: clamp(480px, 36vw, 600px);
		width: min(var(--side-drawer-width), calc(100vw - 24px));
		border-radius: 10px;
		animation: drawer-slide 180ms cubic-bezier(0.25, 1, 0.5, 1);
	}

	@keyframes drawer-slide {
		from {
			opacity: 0;
			transform: translateX(18px);
		}
	}

	@media (max-width: 640px) {
		.drawer-scrim {
			bottom: 0;
		}
		.side-drawer {
			inset: 0;
			width: auto;
			border: 0;
			border-radius: 0;
		}
	}

	:global(.drawer-scroll) {
		overscroll-behavior: contain;
		scrollbar-width: thin;
		scrollbar-color: rgb(var(--line-hover)) transparent;
	}
</style>
