<script lang="ts">
	import { getContext } from 'svelte';
	import tooltip from '$lib/actions/tooltip';
	import Icon from '../Icon.svelte';
	import { INSIDE_TOOLTIP_CONTEXT_KEY } from '../tooltipContext';

	type Size = 'xs' | 'sm' | 'md';
	type Variant = 'ghost' | 'secondary' | 'overlay';

	let {
		icon,
		label,
		size = 'md',
		variant = 'ghost',
		active = false,
		loading = false,
		disabled = false,
		onclick,
		ariaExpanded,
		ariaPressed,
		tooltip: tooltipText,
		kbd,
		class: className = '',
		...rest
	}: {
		icon: string;
		label: string;
		size?: Size;
		variant?: Variant;
		active?: boolean;
		loading?: boolean;
		disabled?: boolean;
		onclick?: (e: MouseEvent) => void;
		ariaExpanded?: boolean;
		ariaPressed?: boolean;
		tooltip?: string;
		kbd?: string | string[];
		class?: string;
		[key: string]: unknown;
	} = $props();

	const insideTooltip = getContext<boolean | undefined>(INSIDE_TOOLTIP_CONTEXT_KEY) === true;

	let isDisabled = $derived(disabled || loading);

	const sizeClasses: Record<Size, string> = {
		xs: 'min-w-6 min-h-6 p-1',
		sm: 'min-w-8 min-h-8 p-1.5',
		md: 'min-w-10 min-h-10 p-2'
	};

	const iconSizeClasses: Record<Size, string> = {
		xs: 'w-3.5 h-3.5',
		sm: 'w-4 h-4',
		md: 'w-5 h-5'
	};

	const variantClasses: Record<Variant, string> = {
		ghost: 'text-fg-muted hover:text-fg hover:bg-surface-3/50',
		secondary: 'bg-surface-3 text-fg hover:bg-line-hover',
		overlay: 'bg-scrim/60 text-scrim-on backdrop-blur-sm hover:bg-scrim/80'
	};

	let classes = $derived(
		`inline-flex items-center justify-center rounded transition-[color,background-color,opacity] duration-100 touch-manipulation disabled:opacity-50 disabled:cursor-not-allowed ${sizeClasses[size]} ${active ? 'bg-signal/10 text-signal' : variantClasses[variant]} ${className}`
	);
</script>

<button
	type="button"
	class={classes}
	aria-label={label}
	aria-expanded={ariaExpanded}
	aria-pressed={ariaPressed}
	aria-busy={loading}
	use:tooltip={insideTooltip ? undefined : { text: tooltipText ?? label, kbd }}
	disabled={isDisabled}
	{onclick}
	{...rest}
>
	{#if loading}
		<span
			class="{iconSizeClasses[
				size
			]} rounded-full border-2 border-line-strong border-t-current animate-spin"
		></span>
	{:else}
		<Icon name={icon} className={iconSizeClasses[size]} />
	{/if}
</button>
