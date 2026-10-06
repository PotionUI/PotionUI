<script lang="ts">
	import { tick } from 'svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import Icon from '$lib/components/Icon.svelte';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';

	let {
		name,
		onRename,
		onDelete
	}: {
		name: string;
		onRename: () => void;
		onDelete: () => void;
	} = $props();

	const WIDTH = 160;

	let open = $state(false);
	let triggerEl = $state<HTMLButtonElement>();
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
			preferred: 'up'
		});
		menuEl.querySelector<HTMLElement>('button')?.focus();
	}

	function toggle() {
		open = !open;
		if (open) void reposition();
		else position = null;
	}

	function close(restoreFocus = false) {
		open = false;
		position = null;
		if (restoreFocus) triggerEl?.focus();
	}

	function choose(action: () => void) {
		close();
		action();
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
			const step = event.key === 'ArrowDown' ? 1 : -1;
			buttons[(index + step + buttons.length) % buttons.length].focus();
		}
	}

	$effect(() => {
		if (!open) return;
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

<button
	bind:this={triggerEl}
	type="button"
	aria-label="More actions for {name}"
	aria-haspopup="menu"
	aria-expanded={open}
	class="inline-flex h-5 w-5 items-center justify-center rounded bg-canvas/80 text-fg hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-signal"
	onclick={toggle}
>
	<Icon name="more" className="h-3.5 w-3.5" />
</button>

{#if open}
	<div
		bind:this={menuEl}
		use:portal
		use:overlayLayer
		role="menu"
		aria-label="Actions for {name}"
		class="fixed rounded-xl border border-line-strong bg-surface-2 p-1 shadow-floating"
		style={menuStyle(position)}
	>
		<button
			type="button"
			role="menuitem"
			class="flex h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-sm text-fg hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none"
			onclick={() => choose(onRename)}
		>
			<Icon name="edit" className="h-4 w-4 flex-shrink-0" />
			<span class="min-w-0 flex-1 truncate">Rename</span>
		</button>
		<button
			type="button"
			role="menuitem"
			class="flex h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-sm text-danger hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none"
			onclick={() => choose(onDelete)}
		>
			<Icon name="trash" className="h-4 w-4 flex-shrink-0" />
			<span class="min-w-0 flex-1 truncate">Delete</span>
		</button>
	</div>
{/if}
