<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';

	type Variant = 'danger' | 'warning' | 'success' | 'info' | 'neutral' | 'signal';
	type Density = 'compact' | 'default';
	/** 'assertive' -> role="alert" (an error the user just caused, e.g. a failed
	 * submit - announced immediately, interrupting). 'polite' -> role="status"
	 * (a passive/persistent notice - announced without interrupting). 'off' for
	 * content that's already announced another way. */
	type Live = 'assertive' | 'polite' | 'off';

	let {
		variant = 'info',
		density = 'default',
		icon,
		title,
		live = variant === 'danger' ? 'assertive' : 'polite',
		class: className = '',
		children,
		actions
	}: {
		variant?: Variant;
		density?: Density;
		/** Icon name, or `true` for the variant's default icon. Omit for no icon. */
		icon?: string | boolean;
		title?: string;
		live?: Live;
		class?: string;
		children?: Snippet;
		actions?: Snippet;
	} = $props();

	const toneClasses: Record<Variant, string> = {
		danger: 'bg-danger/10 border-danger/25 text-danger',
		warning: 'bg-warning/10 border-warning/25 text-warning',
		success: 'bg-success/10 border-success/25 text-success',
		info: 'bg-info/10 border-info/25 text-info',
		neutral: 'bg-surface-2 border-line-strong text-fg-muted',
		signal: 'bg-signal/10 border-signal/25 text-signal'
	};

	const defaultIconByVariant: Record<Variant, string> = {
		danger: 'warning',
		warning: 'warning',
		success: 'check',
		info: 'info',
		neutral: 'info',
		signal: 'info'
	};

	const densityClasses: Record<Density, string> = {
		compact: 'px-3 py-2 text-sm',
		default: 'p-4 text-sm'
	};

	let resolvedIcon = $derived(icon === true ? defaultIconByVariant[variant] : icon || null);
	let role = $derived(live === 'off' ? undefined : live === 'assertive' ? 'alert' : 'status');
	let ariaLive = $derived(live === 'off' ? undefined : live);
</script>

<div
	class="rounded-lg border flex items-start gap-3 {densityClasses[density]} {toneClasses[variant]} {className}"
	{role}
	aria-live={ariaLive}
>
	{#if resolvedIcon}
		<Icon name={resolvedIcon} className="w-4 h-4 flex-shrink-0 {title ? 'mt-0.5' : ''}" />
	{/if}
	<div class="flex-1 min-w-0">
		{#if title}
			<p class="font-medium">{title}</p>
		{/if}
		{#if children}
			<div class={title ? 'mt-0.5' : ''}>
				{@render children()}
			</div>
		{/if}
	</div>
	{#if actions}
		<div class="flex-shrink-0">{@render actions()}</div>
	{/if}
</div>
