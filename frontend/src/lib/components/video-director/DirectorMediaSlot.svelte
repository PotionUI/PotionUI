<script lang="ts">
	import type { DirectorMediaValue } from '$lib/types/videoDirector';
	import type { MediaRef } from '$lib/types/tabs';
	import { resolveDirectorMediaDisplay, collectFormMediaOptions, formMediaOptionKeys, type FormMediaOption } from '$lib/utils/videoDirector';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import MediaThumb from '$lib/components/media/MediaThumb.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';

	let {
		name,
		value,
		formData,
		onChange,
		config = {},
		kind
	}: {
		name: string;
		value: DirectorMediaValue | null;
		formData: Record<string, unknown> | null | undefined;
		onChange: (value: DirectorMediaValue | null) => void;
		config?: Record<string, unknown>;
		/** Narrows the "From form" list to items whose probed type matches. */
		kind?: 'image' | 'video' | 'audio';
	} = $props();

	let display = $derived(resolveDirectorMediaDisplay(value, formData));
	let formOptions = $derived(collectFormMediaOptions(formData, kind));
	let formOptionKeys = $derived(formMediaOptionKeys(formOptions));
	let pickerOpen = $state(false);
	let rootEl: HTMLDivElement | undefined = $state();

	function handleFieldChange(_fieldName: string, v: unknown) {
		onChange((v as MediaRef | null) ?? null);
	}

	function pickFormItem(opt: FormMediaOption) {
		onChange({ form_ref: { field: opt.field, path: opt.item.path } });
		pickerOpen = false;
	}

	function handleWindowMousedown(e: MouseEvent) {
		if (pickerOpen && rootEl && !rootEl.contains(e.target as Node)) pickerOpen = false;
	}
</script>

<svelte:window onmousedown={handleWindowMousedown} />

<div class="flex w-full min-w-0 flex-col items-stretch gap-1.5" bind:this={rootEl}>
	{#if display.kind === 'broken'}
		<div class="flex flex-col items-start gap-1.5 rounded-lg border border-danger/50 bg-danger/5 p-2.5 w-full">
			<div class="flex items-center gap-1.5 text-xs text-danger">
				<Icon name="warning" className="h-3.5 w-3.5 flex-shrink-0" />
				<span>Missing from form: {display.field}</span>
			</div>
			<button
				type="button"
				class="font-mono text-xs font-medium text-fg-muted underline decoration-dotted hover:text-fg"
				onclick={() => onChange(null)}
			>
				Clear
			</button>
		</div>
	{:else}
		<div class="relative min-w-0">
			<MediaLoaderField
				{name}
				value={display.kind === 'empty' ? null : display.media}
				onChange={handleFieldChange}
				{config}
			/>
			{#if display.kind === 'form_ref'}
				<Tooltip text="Linked to form field: {display.field}" position="bottom" wrapperClass="absolute left-1.5 top-1.5 z-10">
					<span class="rounded bg-signal-solid px-1.5 py-0.5 font-mono text-xs font-medium text-white">Linked</span>
				</Tooltip>
			{/if}
		</div>
	{/if}

	{#if formOptions.length > 0}
		<div class="relative">
			<button
				type="button"
				class="inline-flex items-center gap-1 rounded border border-line-strong bg-surface-2 px-2 py-1 font-mono text-xs font-medium text-fg-muted transition-colors hover:border-line-hover hover:bg-surface-3 hover:text-fg"
				onclick={() => (pickerOpen = !pickerOpen)}
				aria-expanded={pickerOpen}
			>
				<Icon name="external-link" className="h-3 w-3" />
				From form
			</button>
			{#if pickerOpen}
				<div class="absolute left-0 top-full z-20 mt-1 max-h-64 w-56 overflow-y-auto rounded-lg border border-line-strong bg-surface-1 p-1 shadow-floating">
					{#each formOptions as opt, i (formOptionKeys[i])}
						<button
							type="button"
							class="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-xs text-fg hover:bg-surface-2"
							onclick={() => pickFormItem(opt)}
						>
							<span class="flex h-8 w-8 flex-shrink-0 items-center justify-center overflow-hidden rounded border border-line bg-surface-2">
								<MediaThumb
									url={opt.item.url}
									kind={opt.item.type}
									name={opt.item.label || opt.item.name}
									className="h-full w-full"
									rounded={false}
									iconClassName="h-3.5 w-3.5 text-fg-subtle"
								/>
							</span>
							<span class="min-w-0 flex-1">
								<span class="block truncate text-fg">{opt.item.label || opt.item.name || 'Untitled'}</span>
								<span class="block truncate text-fg-subtle">{opt.fieldLabel}</span>
							</span>
						</button>
					{/each}
				</div>
			{/if}
		</div>
	{/if}
</div>
