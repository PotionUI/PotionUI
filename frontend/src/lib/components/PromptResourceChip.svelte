<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import Icon from './Icon.svelte';
	import MediaThumb from './media/MediaThumb.svelte';
	import { kindLabel, resourceHandleLabel, type PromptResourceSpec } from '$lib/utils/promptResources';

	export let field: string;
	export let itemKey: string;
	export let spec: PromptResourceSpec | null = null;
	export let position: number | null = null;
	export let item: unknown = undefined;
	export let fieldLabel: string | undefined = undefined;
	export let disabled: boolean = false;
	export let onRemove: (() => void) | undefined = undefined;
	export let onSwitch: (() => void) | undefined = undefined;
	export let variant: 'default' | 'segment-composer' = 'default';

	$: dangling = !spec || position === null;
	$: canSwitch = Boolean(onSwitch) && !disabled;

	function handleChipClick() {
		if (canSwitch) onSwitch?.();
	}

	function handleChipKeydown(e: KeyboardEvent) {
		if (!canSwitch) return;
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			onSwitch?.();
		}
	}
	$: displayLabel = spec && position !== null ? resourceHandleLabel(spec, position) : field;
	$: resolvedFieldLabel = fieldLabel || field;
	$: itemUrl = item && typeof item === 'object' && typeof (item as Record<string, unknown>).url === 'string'
		? ((item as Record<string, unknown>).url as string)
		: null;
	$: itemName =
		item && typeof item === 'object' && typeof (item as Record<string, unknown>).name === 'string'
			? ((item as Record<string, unknown>).name as string)
			: itemKey.split('/').pop() || itemKey;
	$: kindIcon = spec ? (spec.kind === 'image' ? 'image' : spec.kind === 'video' ? 'video' : 'audio') : 'warning';
	$: tooltipText = dangling
		? spec
			? `Removed from ${resolvedFieldLabel}`
			: `${resolvedFieldLabel} can no longer be referenced here`
		: `${kindLabel(spec!.kind)} · ${itemName}`;
</script>

{#if variant === 'segment-composer'}
	<span
		class="chip resource-chip {dangling ? 'border-danger/50 bg-danger/10 text-danger dangling' : ''} {disabled
			? 'opacity-50'
			: ''}"
		contenteditable="false"
		role="button"
		tabindex={canSwitch ? 0 : -1}
		on:click={handleChipClick}
		on:keydown={handleChipKeydown}
	>
		<Tooltip text={tooltipText}>
			<span class="chip-main">
				<span class="chip-thumb">
					{#if !dangling}
						<MediaThumb url={itemUrl} kind={spec?.kind} name={itemName} className="w-full h-full" rounded={false} iconClassName="icon" />
					{:else}
						<Icon name={kindIcon} className="icon" />
					{/if}
				</span>
				<span class="chip-label">{displayLabel}</span>
			</span>
		</Tooltip>
	</span>
{:else}
	<span
		class="resource-chip group inline-flex items-center rounded border transition-colors duration-100 mx-1 {dangling
			? 'border-danger/50 bg-danger/10 text-danger'
			: 'border-line bg-surface-2 text-fg-muted'}"
		contenteditable="false"
	>
		<Tooltip text={tooltipText} wrapperClass="inline-flex items-center">
			<span class="relative inline-flex items-center gap-1.5 py-1 pl-1.5 pr-1.5">
				{#if dangling}
					<Icon name={kindIcon} className="w-3.5 h-3.5 flex-shrink-0" />
				{:else}
					<MediaThumb
						url={itemUrl}
						kind={spec?.kind}
						name={itemName}
						className="w-3.5 h-3.5 rounded-sm flex-shrink-0"
						rounded={false}
						iconClassName="w-3.5 h-3.5"
					/>
				{/if}
				<span class="text-xs font-medium whitespace-nowrap font-mono tabular-nums">{displayLabel}</span>
			</span>
		</Tooltip>
		{#if dangling && !disabled}
			<Tooltip text="Remove reference">
				<button
					type="button"
					class="inline-flex items-center justify-center pr-1.5 pl-0.5 text-danger/70 hover:text-danger transition-colors duration-100"
					on:mousedown|preventDefault|stopPropagation={() => onRemove?.()}
					aria-label="Remove reference"
				>
					<Icon name="close" className="w-3 h-3" />
				</button>
			</Tooltip>
		{/if}
	</span>
{/if}
