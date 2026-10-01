<script lang="ts">
	import { tick } from 'svelte';
	import { browser } from '$app/environment';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { IconButton } from '$lib/components/ui';
	import type { MenuAction } from '$lib/formulas/types';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';

	

	let {
		name,
		onAction
	}: {
		name: string;
		onAction: (action: MenuAction) => void;
	} = $props();

	const WIDTH = 220;
	const items: Array<{ id: MenuAction; label: string; icon: string; danger?: boolean }> = [
		{ id: 'rename', label: 'Rename', icon: 'edit' },
		{ id: 'duplicate', label: 'Duplicate', icon: 'copy' },
		{ id: 'update', label: 'Update from current form', icon: 'refresh' },
		{ id: 'delete', label: 'Delete', icon: 'trash', danger: true }
	];

	let open = $state(false);
	let triggerEl = $state<HTMLElement>();
	let menuEl = $state<HTMLElement>();
	let position = $state<FlippedMenuPosition | null>(null);

	async function reposition() {
		await tick();
		if (!open || !triggerEl || !menuEl) return;
		position = computeFlippedMenuPosition(triggerEl, {
			width: WIDTH,
			heightEstimate: menuEl.offsetHeight,
			gap: 4,
			align: 'right',
			preferred: 'down'
		});
		menuEl.querySelector<HTMLElement>('button')?.focus();
	}

	function toggle() {
		open = !open;
		if (open) reposition();
		else position = null;
	}

	function close(restoreFocus = false) {
		open = false;
		position = null;
		if (restoreFocus) triggerEl?.querySelector('button')?.focus();
	}

	function choose(action: MenuAction) {
		close();
		onAction(action);
	}

	function menuStyle(pos: FlippedMenuPosition | null): string {
		if (!pos) return `visibility: hidden; top: 0; left: 0; width: ${WIDTH}px;`;
		const vertical = pos.top !== undefined ? `top: ${pos.top}px;` : `bottom: ${pos.bottom}px;`;
		return `${vertical} left: ${pos.left}px; width: ${WIDTH}px;`;
	}

	function onWindowPointer(event: PointerEvent) {
		const target = event.target as Node;
		if (menuEl?.contains(target) || triggerEl?.contains(target)) return;
		close();
	}

	function onWindowKey(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			close(true);
		} else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			const buttons = Array.from(menuEl?.querySelectorAll<HTMLElement>('button') ?? []);
			if (buttons.length === 0) return;
			event.preventDefault();
			event.stopPropagation();
			const index = buttons.indexOf(document.activeElement as HTMLElement);
			const next = event.key === 'ArrowDown' ? (index + 1) % buttons.length : (index - 1 + buttons.length) % buttons.length;
			buttons[next].focus();
		}
	}

	$effect(() => {
		if (!browser || !open) return;
		window.addEventListener('pointerdown', onWindowPointer, true);
		window.addEventListener('keydown', onWindowKey, true);
		window.addEventListener('resize', reposition);
		window.addEventListener('scroll', reposition, true);
		return () => {
			window.removeEventListener('pointerdown', onWindowPointer, true);
			window.removeEventListener('keydown', onWindowKey, true);
			window.removeEventListener('resize', reposition);
			window.removeEventListener('scroll', reposition, true);
		};
	});
</script>

<span bind:this={triggerEl} class="inline-flex">
	<Tooltip text="More actions" position="left" delay={150}>
		<IconButton icon="more" label={`More actions for ${name}`} size="sm" ariaExpanded={open} onclick={toggle} />
	</Tooltip>
</span>

{#if open}
	<div
		bind:this={menuEl}
		use:portal
		use:overlayLayer
		role="menu"
		aria-label={`Actions for ${name}`}
		class="fixed rounded-xl border border-line-strong bg-surface-2 p-1 shadow-floating"
		style={menuStyle(position)}
	>
		{#each items as item (item.id)}
			<button
				type="button"
				role="menuitem"
				class="flex h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-sm hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none {item.danger ? 'text-danger' : 'text-fg'}"
				onclick={() => choose(item.id)}
			>
				<Icon name={item.icon} className="h-4 w-4 flex-shrink-0" />
				<span class="min-w-0 flex-1 truncate">{item.label}</span>
			</button>
		{/each}
	</div>
{/if}
