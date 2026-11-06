<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import tooltip from '$lib/actions/tooltip';
	import type { RailGroup, RailTool } from '$lib/tool-tabs/frame/types';

	let {
		groups,
		active = null,
		pending = [],
		variant = 'rail',
		label = 'Tools',
		onselect
	}: {
		groups: RailGroup[];
		active?: string | null;
		pending?: string[];
		variant?: 'rail' | 'icons' | 'strip';
		label?: string;
		onselect: (id: string) => void;
	} = $props();

	let root = $state<HTMLElement>();
	let strip = $derived(variant === 'strip');
	let icons = $derived(variant === 'icons');

	function unavailable(tool: RailTool): boolean {
		return !!tool.disabled || !!tool.later;
	}

	function reason(tool: RailTool): string | null {
		if (tool.reason) return tool.reason;
		return tool.later ? 'Later' : null;
	}

	function ariaLabel(tool: RailTool): string {
		const parts = [tool.label];
		if (pending.includes(tool.id)) parts.push('pending');
		const why = unavailable(tool) ? reason(tool) : null;
		if (why) parts.push(why);
		return parts.join(', ');
	}

	function tip(tool: RailTool) {
		if (unavailable(tool)) return { text: reason(tool), position: strip ? ('top' as const) : ('right' as const) };
		if (!icons) return null;
		return { text: tool.label, position: 'right' as const, kbd: tool.keys };
	}

	function pick(tool: RailTool) {
		if (unavailable(tool)) return;
		onselect(tool.id);
	}

	function onkeydown(event: KeyboardEvent) {
		const keys = strip ? ['ArrowLeft', 'ArrowRight'] : ['ArrowUp', 'ArrowDown'];
		if (![...keys, 'Home', 'End'].includes(event.key) || !root) return;
		const buttons = Array.from(root.querySelectorAll<HTMLButtonElement>('button[data-tool]'));
		const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
		if (at < 0) return;
		event.preventDefault();
		const next =
			event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : event.key === keys[0] ? Math.max(0, at - 1) : Math.min(buttons.length - 1, at + 1);
		buttons[next].focus();
	}

	$effect(() => {
		if (!strip || !root || !active) return;
		const target = active;
		Array.from(root.querySelectorAll<HTMLElement>('button[data-tool]'))
			.find((button) => button.dataset.tool === target)
			?.scrollIntoView({ block: 'nearest', inline: 'center' });
	});
</script>

<div
	bind:this={root}
	class={strip
		? 'strip flex h-16 flex-none gap-0.5 overflow-x-auto border-t border-line bg-surface-1 px-1.5 py-1'
		: icons
			? 'rail flex w-[52px] flex-none flex-col items-center gap-0.5 overflow-y-auto border-r border-line bg-surface-1 px-1.5 py-2'
			: 'rail flex w-[76px] flex-none flex-col gap-0.5 overflow-y-auto border-r border-line bg-surface-1 px-1.5 py-2'}
	role="toolbar"
	aria-orientation={strip ? 'horizontal' : 'vertical'}
	aria-label={label}
	tabindex="-1"
	{onkeydown}
	data-tool-rail={variant}
>
	{#each groups as group, g (group.id)}
		{#if icons}
			{#if g > 0}
				<div class="my-1.5 h-px w-6 flex-none bg-line" role="separator" aria-hidden="true"></div>
			{/if}
		{:else if !strip}
			<div
				class="text-center font-mono text-sm uppercase tracking-[0.06em] text-fg-subtle {g === 0 ? 'pb-1 pt-0.5' : 'pb-1 pt-2.5'}"
				role="presentation"
			>
				{group.label}
			</div>
		{/if}
		{#each group.tools as tool (tool.id)}
			{@const on = tool.id === active}
			{@const off = unavailable(tool)}
			<button
				type="button"
				class="tool relative flex items-center rounded text-center text-sm leading-[14px] {icons
					? 'h-10 w-10 flex-none justify-center'
					: `flex-col gap-[3px] px-0.5 pb-1.5 pt-[7px] ${strip ? 'w-16 flex-none justify-center' : ''}`} {on
					? 'is-on bg-signal/10 text-signal'
					: off
						? 'cursor-not-allowed text-fg-disabled'
						: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
				aria-pressed={on}
				aria-disabled={off || undefined}
				aria-label={ariaLabel(tool)}
				data-tool={tool.id}
				use:tooltip={tip(tool)}
				onclick={() => pick(tool)}
			>
				<Icon name={tool.icon} className="h-[18px] w-[18px] flex-none" strokeWidth={1.7} />
				{#if !icons}
					<span class="max-w-full truncate">{tool.label}</span>
				{/if}
				{#if pending.includes(tool.id)}
					<span class="absolute right-1.5 top-1 h-1.5 w-1.5 rounded-full bg-warning-solid" aria-hidden="true" data-tool-pending></span>
				{/if}
			</button>
		{/each}
	{/each}
</div>

<style>
	.rail,
	.strip {
		scrollbar-width: none;
	}

	.strip {
		mask-image: linear-gradient(90deg, rgb(var(--scrim)) 85%, transparent);
	}

	.rail .is-on::before {
		content: '';
		position: absolute;
		left: -6px;
		top: 8px;
		bottom: 8px;
		width: 2px;
		background: rgb(var(--signal));
	}

	.strip .is-on::before {
		content: '';
		position: absolute;
		left: 10px;
		right: 10px;
		bottom: -4px;
		height: 2px;
		background: rgb(var(--signal));
	}
</style>
