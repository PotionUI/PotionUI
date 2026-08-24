<script lang="ts">
	import { getContext } from 'svelte';
	import Icon from '../Icon.svelte';
	import { INSIDE_TOOLTIP_CONTEXT_KEY } from '../tooltipContext';

	type Size = 'sm' | 'md';
	type Variant = 'ghost' | 'secondary';

	let {
		icon,
		label,
		size = 'md',
		variant = 'ghost',
		active = false,
		disabled = false,
		onclick,
		ariaExpanded,
		class: className = ''
	}: {
		icon: string;
		label: string;
		size?: Size;
		variant?: Variant;
		active?: boolean;
		disabled?: boolean;
		onclick?: (e: MouseEvent) => void;
		ariaExpanded?: boolean;
		class?: string;
	} = $props();

	// A wrapping Tooltip already shows `label`; keeping the native title too
	// renders two tooltips side by side.
	const insideTooltip = getContext<boolean | undefined>(INSIDE_TOOLTIP_CONTEXT_KEY) === true;

	const sizeClasses: Record<Size, string> = {
		sm: 'min-w-8 min-h-8 p-1.5',
		md: 'min-w-10 min-h-10 p-2'
	};

	const iconSizeClasses: Record<Size, string> = {
		sm: 'w-4 h-4',
		md: 'w-5 h-5'
	};

	const variantClasses: Record<Variant, string> = {
		ghost: 'text-fg-muted hover:text-fg hover:bg-surface-3/50',
		secondary: 'bg-surface-3 text-fg hover:bg-line-hover'
	};

	let classes = $derived(
		`inline-flex items-center justify-center rounded transition-colors duration-100 touch-manipulation disabled:opacity-50 disabled:cursor-not-allowed ${sizeClasses[size]} ${active ? 'bg-signal/10 text-signal' : variantClasses[variant]} ${className}`
	);
</script>

<button
	type="button"
	class={classes}
	aria-label={label}
	aria-expanded={ariaExpanded}
	title={insideTooltip ? undefined : label}
	{disabled}
	{onclick}
>
	<Icon name={icon} className={iconSizeClasses[size]} />
</button>
