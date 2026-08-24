<script lang="ts">
	import FieldChildren from './FieldChildren.svelte';
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';

	export let name: string | null;
	export let config: any;
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	export let onOriginChange: ((fieldName: string, origin: unknown) => void) | undefined = undefined;
	export let onMaskChange: ((fieldName: string, maskPath: string | undefined) => void) | undefined = undefined;
	export let fieldPath: string | undefined = undefined;

	$: label = config.title || name || '';

	const isCollapsed = config.configuration?.collapsed ?? false;
	const isCollapsible = config.configuration?.collapsible ?? true;
	const accordionIcon = config.configuration?.icon;
	const accordionTooltip = config.tooltip || config.configuration?.tooltip;
	let isExpanded = !isCollapsed;
</script>

<div class="border-l-2 border-line-strong pl-3">
	<button
		type="button"
		on:click={() => isCollapsible && (isExpanded = !isExpanded)}
		class="w-full py-1 text-left flex items-center justify-between hover:bg-surface-2 rounded transition-colors"
		disabled={!isCollapsible}
	>
		<div class="flex items-center gap-2">
			{#if accordionIcon}
				<Icon name={accordionIcon} className="w-4 h-4 text-fg-muted" />
			{/if}
			<span class="font-mono text-xs font-semibold text-fg-muted uppercase tracking-[0.08em]">{config.label || label}</span>
			{#if accordionTooltip}
				<Tooltip text={accordionTooltip} position="top">
					<span class="text-fg-subtle cursor-help inline-flex items-center">
						<Icon name="info" className="w-3.5 h-3.5" />
					</span>
				</Tooltip>
			{/if}
		</div>
		{#if isCollapsible}
			<svg
				class="w-3 h-3 text-fg-subtle transition-transform {isExpanded ? 'rotate-180' : ''}"
				fill="none"
				viewBox="0 0 24 24"
				stroke="currentColor"
			>
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width={2} d="M19 9l-7 7-7-7" />
			</svg>
		{/if}
	</button>

	<!-- Content is always mounted, animated via max-height CSS transition -->
	<div
		class="{isExpanded
			? 'overflow-visible'
			: 'overflow-hidden'} transition-[max-height,opacity] duration-300 ease-out motion-reduce:duration-0"
		style="max-height: {isExpanded ? '2000px' : '0px'}; opacity: {isExpanded ? 1 : 0};"
	>
		{#if config.children}
			<fieldset class="border-0 p-0 m-0 min-w-0 space-y-4">
				<legend class="sr-only">{config.label || label}</legend>
				<FieldChildren children={config.children} {value} {onChange} {onOriginChange} {onMaskChange} location="accordion" {fieldPath} />
			</fieldset>
		{/if}
	</div>
</div>
