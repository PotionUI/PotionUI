<script lang="ts">
	import { untrack } from 'svelte';
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import { KIT_TEXT_LIMIT } from '$lib/kits/types';

	let {
		title = 'Write text',
		initial = '',
		confirmLabel = 'Add text',
		onSave,
		onClose
	}: {
		title?: string;
		initial?: string;
		confirmLabel?: string;
		onSave: (text: string) => Promise<string | null>;
		onClose: () => void;
	} = $props();

	let text = $state(untrack(() => initial));
	let busy = $state(false);
	let error = $state<string | null>(null);

	let used = $derived(text.length);
	let empty = $derived(text.trim().length === 0);

	async function save(): Promise<boolean> {
		busy = true;
		error = null;
		const problem = await onSave(text.trim());
		busy = false;
		if (problem) {
			error = problem;
			return false;
		}
		onClose();
		return true;
	}
</script>

<ConfirmDialog isOpen={true} size="md" {title} {busy} {confirmLabel} confirmDisabled={empty || used > KIT_TEXT_LIMIT} onConfirm={save} onCancel={onClose}>
	<div class="space-y-2 px-6 py-5">
		<label class="block text-sm text-fg-muted" for="kit-text">Text</label>
		<textarea
			id="kit-text"
			class="input min-h-[168px] w-full resize-y text-base leading-relaxed"
			bind:value={text}
			maxlength={KIT_TEXT_LIMIT}
			placeholder="A description, a line of dialogue, a note the model should follow"
			data-autofocus
		></textarea>
		<div class="flex items-center justify-between gap-3 text-sm">
			<span class="text-fg-subtle">Text is added to the prompt where you call it.</span>
			<span class="font-mono tabular-nums {used > KIT_TEXT_LIMIT * 0.95 ? 'text-warning' : 'text-fg-subtle'}">{used} / {KIT_TEXT_LIMIT}</span>
		</div>
		{#if error}<p class="text-sm text-danger" role="alert">{error}</p>{/if}
	</div>
</ConfirmDialog>
