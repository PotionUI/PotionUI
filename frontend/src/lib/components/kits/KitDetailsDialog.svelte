<script lang="ts">
	import { untrack } from 'svelte';
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import { Button, Input } from '$lib/components/ui';
	import { checkName, liveName, suggestFree } from '$lib/kits/kitNaming';
	import type { KitDetail, KitLabelCount, KitPatch } from '$lib/kits/types';

	let {
		kit,
		labels,
		onSave,
		onDelete,
		onClose
	}: {
		kit: KitDetail;
		labels: KitLabelCount[];
		onSave: (patch: KitPatch) => Promise<{ error: string | null; clash?: boolean }>;
		onDelete: () => void;
		onClose: () => void;
	} = $props();

	let name = $state(untrack(() => kit.name));
	let handle = $state(untrack(() => kit.handle));
	let label = $state(untrack(() => kit.label ?? ''));
	let busy = $state(false);
	let error = $state<string | null>(null);
	let clash = $state(false);

	let handleCheck = $derived(checkName(handle));
	let changed = $derived(name.trim() !== kit.name || handle !== kit.handle || label.trim() !== (kit.label ?? ''));
	let valid = $derived(name.trim().length > 0 && handleCheck.ok);

	async function save(): Promise<boolean> {
		busy = true;
		error = null;
		clash = false;
		const patch: KitPatch = {};
		if (name.trim() !== kit.name) patch.name = name.trim();
		if (handle !== kit.handle) patch.handle = handle;
		if (label.trim() !== (kit.label ?? '')) patch.label = label.trim() || null;
		const result = await onSave(patch);
		busy = false;
		if (result.error) {
			error = result.error;
			clash = !!result.clash;
			return false;
		}
		onClose();
		return true;
	}
</script>

<ConfirmDialog
	isOpen={true}
	submitOnInputEnter
	size="md"
	title="Kit details"
	{busy}
	confirmLabel="Save"
	confirmDisabled={!changed || !valid}
	onConfirm={save}
	onCancel={onClose}
>
	{#snippet leftActions()}
		<Button variant="ghost" size="md" icon="trash" class="text-danger" onclick={onDelete}>Delete…</Button>
	{/snippet}
	<div class="space-y-5 p-4 md:p-5">
		<div>
			<label class="label" for="kit-name">Name</label>
			<Input id="kit-name" bind:value={name} autocomplete="off" class="text-base" data-autofocus />
		</div>
		<div>
			<label class="label" for="kit-handle">Path name</label>
			<div
				class="flex items-center overflow-hidden rounded border bg-field-bg focus-within:border-signal {handleCheck.ok && !clash ? 'border-field-border' : 'border-danger'}"
			>
				<span class="border-r border-field-border px-2.5 font-mono text-base text-fg-subtle">@kits.</span>
				<input
					id="kit-handle"
					class="h-9 min-w-0 flex-1 border-0 bg-transparent px-2.5 font-mono text-base tabular-nums text-fg outline-none"
					value={handle}
					autocomplete="off"
					aria-invalid={!handleCheck.ok || clash}
					oninput={(event) => {
						const input = event.currentTarget as HTMLInputElement;
						handle = liveName(input.value);
						input.value = handle;
						clash = false;
					}}
				/>
			</div>
			{#if clash}
				<p class="mt-1.5 text-sm text-danger" role="alert">
					Already used. <button type="button" class="font-mono underline" onclick={() => { handle = suggestFree(handle, [handle]); clash = false; }}>{suggestFree(handle, [handle])}</button>
				</p>
			{:else if !handleCheck.ok && handleCheck.message}
				<p class="mt-1.5 text-sm text-danger" role="alert">{handleCheck.message}</p>
			{:else}
				<p class="mt-1.5 text-sm text-fg-subtle">Lowercase letters, digits and _, starting with a letter. Prompts keep working if you change it.</p>
			{/if}
		</div>
		<div>
			<label class="label" for="kit-label">Label</label>
			<Input id="kit-label" bind:value={label} autocomplete="off" class="text-base" placeholder="Actor, Location, Prop, or anything" />
			{#if labels.length > 0}
				<div class="mt-2 flex flex-wrap items-center gap-1.5">
					<span class="text-sm text-fg-subtle">Yours</span>
					{#each labels as entry (entry.label)}
						<button
							type="button"
							class="inline-flex h-6 items-center rounded border px-2 text-sm {entry.label === label
								? 'border-signal/25 bg-signal/10 text-signal'
								: 'border-line-strong bg-surface-2 text-fg-muted hover:text-fg'}"
							aria-pressed={entry.label === label}
							onclick={() => (label = entry.label)}
						>
							{entry.label}
						</button>
					{/each}
				</div>
			{/if}
			<p class="mt-1.5 text-sm text-fg-subtle">A label only helps you find kits. Nothing in the app reads it.</p>
		</div>
		{#if error && !clash}<p class="text-sm text-danger" role="alert">{error}</p>{/if}
	</div>
</ConfirmDialog>
