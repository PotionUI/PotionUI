<script lang="ts">
	// Wraps MediaLoaderField with Stage B "global reference media": a Director
	// media slot can point at an item living on the generate FORM's own
	// media-loader field(s) (`FormMediaRef`) instead of embedding its own copy.
	// Resolution against the live form, and the broken-reference state, are
	// pure logic in utils/videoDirector.ts (resolveDirectorMediaDisplay) -- this
	// component only renders what that returns and turns picks into
	// `onChange` calls. A plain upload/library/history pick through the wrapped
	// MediaLoaderField always produces a fresh embedded value, never a
	// `form_ref` -- only the "From form" list below mints one.
	import type { DirectorMediaValue } from '$lib/types/videoDirector';
	import type { MediaRef } from '$lib/types/tabs';
	import { resolveDirectorMediaDisplay, collectFormMediaOptions, formMediaOptionKeys, type FormMediaOption } from '$lib/utils/videoDirector';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import Icon from '$lib/components/Icon.svelte';

	let {
		name,
		value,
		formData,
		onChange,
		config = {},
		compact = true,
		compactFullWidth = false,
		fill = false,
		kind
	}: {
		name: string;
		value: DirectorMediaValue | null;
		formData: Record<string, unknown> | null | undefined;
		onChange: (value: DirectorMediaValue | null) => void;
		config?: Record<string, unknown>;
		compact?: boolean;
		compactFullWidth?: boolean;
		/** Stretch the slot to fill the host box and show full labelled buttons. */
		fill?: boolean;
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

<div class="{fill ? 'flex h-full w-full flex-col items-stretch' : 'inline-flex flex-col items-start'} gap-1.5" bind:this={rootEl}>
	{#if display.kind === 'broken'}
		<div class="flex flex-col items-start gap-1.5 rounded-lg border border-danger/50 bg-danger/5 p-2.5 {fill || compactFullWidth ? 'w-full' : 'max-w-[200px]'}">
			<div class="flex items-center gap-1.5 text-2xs text-danger">
				<Icon name="warning" className="h-3.5 w-3.5 flex-shrink-0" />
				<span>Missing from form: {display.field}</span>
			</div>
			<button
				type="button"
				class="font-mono text-2xs font-medium text-fg-muted underline decoration-dotted hover:text-fg"
				onclick={() => onChange(null)}
			>
				Clear
			</button>
		</div>
	{:else}
		<div class="relative {fill ? 'min-h-0 flex-1' : ''}">
			<MediaLoaderField
				{name}
				value={display.kind === 'empty' ? null : display.media}
				onChange={handleFieldChange}
				{config}
				{compact}
				{compactFullWidth}
				{fill}
			/>
			{#if display.kind === 'form_ref'}
				<span
					class="pointer-events-none absolute left-1.5 top-1.5 z-10 rounded bg-signal px-1.5 py-0.5 font-mono text-2xs font-medium text-canvas"
					title="Linked to form field: {display.field}"
				>
					Linked
				</span>
			{/if}
		</div>
	{/if}

	{#if formOptions.length > 0}
		<div class="relative">
			<button
				type="button"
				class="inline-flex items-center gap-1 rounded border border-line-strong bg-surface-2 px-2 py-1 font-mono text-2xs font-medium text-fg-muted transition-colors hover:border-line-hover hover:bg-surface-3 hover:text-fg"
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
							class="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-2xs text-fg hover:bg-surface-2"
							onclick={() => pickFormItem(opt)}
						>
							<span class="flex h-8 w-8 flex-shrink-0 items-center justify-center overflow-hidden rounded border border-line bg-surface-2">
								{#if opt.item.type === 'image' && opt.item.url}
									<img src={opt.item.url} alt="" class="h-full w-full object-cover" />
								{:else}
									<Icon
										name={opt.item.type === 'video' ? 'video' : opt.item.type === 'audio' ? 'audio' : 'image'}
										className="h-3.5 w-3.5 text-fg-subtle"
									/>
								{/if}
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
