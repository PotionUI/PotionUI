<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';

	interface Props {
		icon: string;
		label: string;
		tip?: string | null;
		disabled?: boolean;
		pressed?: boolean;
		haspopup?: boolean;
		large?: boolean;
		onclick?: (event: MouseEvent) => void;
	}

	let { icon, label, tip = null, disabled = false, pressed = undefined, haspopup = false, large = false, onclick }: Props = $props();
</script>

<Tooltip text={tip ?? label} position="top">
	<button
		type="button"
		class="node-icon-button inline-flex shrink-0 items-center justify-center rounded text-fg-muted"
		class:large
		aria-label={label}
		aria-pressed={pressed}
		aria-haspopup={haspopup ? 'menu' : undefined}
		aria-disabled={disabled}
		onclick={(event) => {
			event.stopPropagation();
			if (!disabled) onclick?.(event);
		}}
	>
		<Icon name={icon} className="h-4 w-4" />
	</button>
</Tooltip>

<style>
	.node-icon-button {
		width: 28px;
		height: 28px;
	}
	.node-icon-button.large {
		width: 40px;
		height: 40px;
	}
	.node-icon-button:hover {
		background: rgb(var(--surface-2));
		color: rgb(var(--fg));
	}
	.node-icon-button[aria-pressed='true'] {
		color: rgb(var(--signal));
	}
	.node-icon-button[aria-disabled='true'] {
		opacity: 0.5;
		cursor: not-allowed;
	}
	:global(:where(html:not([data-input='pointer']))) .node-icon-button:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 1px;
	}
</style>
